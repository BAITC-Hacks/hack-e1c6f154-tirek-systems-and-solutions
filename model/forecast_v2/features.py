"""Forecast-origin features. Monthly values never become daily observations/labels."""
import numpy as np
import pandas as pd

HORIZON = 28


def monthly_projection(monthly, dates, year):
    """28-day *forecast proxy*, weighted by calendar overlap of monthly aggregates.

    This is an explicit within-month constant-rate approximation, not a daily series.
    Missing monthly values stay missing; negative net totals only floored in forecast proxy.
    """
    result = np.zeros(len(monthly))
    valid = np.ones(len(monthly), dtype=bool)
    months, counts = np.unique(pd.DatetimeIndex(dates).month, return_counts=True)
    for month, count in zip(months, counts):
        stamp = pd.Timestamp(year, int(month), 1)
        if stamp not in monthly.columns:
            return np.full(len(monthly), np.nan)
        values = monthly[stamp].to_numpy(float)
        valid &= np.isfinite(values)
        result += np.nan_to_num(np.maximum(values, 0)) * count / stamp.days_in_month
    return np.where(valid, result, np.nan)


def features_at(panel, origin, target=False, history2024=True):
    origin = pd.Timestamp(origin).normalize()
    if origin not in panel.daily.columns:
        raise ValueError("Origin outside transaction calendar")
    pos = panel.daily.columns.get_loc(origin)
    if pos < 167:
        raise ValueError("At least 168 daily calendar observations required")
    hist = panel.daily.iloc[:, :pos + 1]
    seen = hist.gt(0).any(axis=1)
    hist = hist.loc[seen]
    skus = hist.index
    h = hist.to_numpy(float)
    first = np.argmax(h > 0, axis=1)
    last = h.shape[1] - 1 - np.argmax((h > 0)[:, ::-1], axis=1)
    future_dates = pd.date_range(origin + pd.Timedelta(days=1), periods=HORIZON)
    mid = future_dates[13]
    d = {"sku": skus.astype(str), "origin": origin, "label_end": origin + pd.Timedelta(days=28),
         "age_days": pos - first + 1, "days_since_sale": pos - last,
         "month_sin": np.sin(2 * np.pi * mid.dayofyear / 365.25),
         "month_cos": np.cos(2 * np.pi * mid.dayofyear / 365.25), "target_month": mid.month}
    for size in (7, 14, 28, 56, 84, 168, 364):
        w = h[:, -size:]
        d[f"rate{size}"] = w.sum(axis=1) * 28 / w.shape[1]
    for j in range(1, 7):
        d[f"block{j}"] = h[:, -28*j:][:, :28].sum(axis=1)
    for size in (28, 84, 168):
        w = h[:, -size:]
        d[f"nonzero{size}"] = (w > 0).sum(axis=1)
        d[f"max{size}"] = w.max(axis=1)
        d[f"std{size}"] = w.std(axis=1)
    weeks = h[:, -168:].reshape(len(h), 24, 7).sum(axis=2)
    d["active_weeks"] = (weeks > 0).sum(axis=1)
    nonzero = np.maximum(d["active_weeks"], 1)
    event_mean = weeks.sum(axis=1) / nonzero
    event_var = (weeks ** 2).sum(axis=1) / nonzero - event_mean ** 2
    d["weekly_cv2"] = np.maximum(0, event_var) / np.maximum(event_mean ** 2, 1)
    d["weekly_adi"] = 24 / nonzero
    d["group"] = np.select([d["age_days"] < 84, d["nonzero84"] == 0,
                            d["weekly_adi"] >= 1.32, d["weekly_cv2"] >= .49],
                           ["new", "inactive", "intermittent", "erratic"], default="regular")
    # ABC boundaries based exclusively on past observed volume at this origin.
    mass = d["rate364"]
    order = np.argsort(-mass, kind="stable")
    cumulative_before = (np.cumsum(mass[order]) - mass[order]) / max(mass.sum(), 1)
    abc = np.empty(len(h), dtype=object)
    abc[order] = np.select([cumulative_before < .8, cumulative_before < .95], ["A", "B"], default="C")
    d["abc"] = abc
    for size in (28, 56, 84):
        start = pos + 1 - 364 - (size - 28) // 2
        d[f"year{size}"] = h[:, start:start + size].sum(axis=1) * 28 / size if start >= 0 else np.full(len(h), np.nan)
    # Full monthly table with strict precedence. Only 2024 comes from monthly export.
    monthly = hist.T.resample("MS").sum().T
    monthly = monthly.loc[:, monthly.columns + pd.offsets.MonthEnd(0) < origin]
    if history2024:
        older = panel.monthly_2024.reindex(skus)
        older = older.loc[:, older.columns + pd.offsets.MonthEnd(0) < origin]
        monthly = pd.concat([older, monthly], axis=1)
    d["monthly2024_available"] = monthly.reindex(columns=pd.date_range("2024-01-01", periods=12, freq="MS")).notna().all(axis=1).astype(int).to_numpy()
    prior = monthly_projection(monthly, future_dates, origin.year - 1)
    prior2 = monthly_projection(monthly, future_dates, origin.year - 2)
    d["monthly_year28"], d["monthly_2year28"] = prior, prior2
    # Ratios of future vs recent seasonal shape, within each completed historical year.
    season_ratios = []
    for year in (origin.year - 1, origin.year - 2):
        recent_proxy = monthly_projection(monthly, pd.date_range(origin - pd.Timedelta(days=83), origin), year) / 3
        fut = monthly_projection(monthly, future_dates, year)
        season_ratios.append(np.where(recent_proxy > 0, fut / np.maximum(recent_proxy, 1), np.nan))
    available = np.isfinite(np.vstack(season_ratios)).sum(axis=0)
    ratio = np.nansum(season_ratios, axis=0) / np.maximum(available, 1)
    d["monthly_season_ratio"] = np.where(available, np.clip(ratio, .25, 4), 1)
    d["monthly_season_rate"] = d["rate84"] * d["monthly_season_ratio"]
    d["year_trend"] = np.clip((d["rate84"] + 10) / (np.nan_to_num(d["year84"], nan=0) + 10), .5, 2)
    d["scale"] = np.maximum(d["rate84"], 10)
    frame = pd.DataFrame(d)
    if target:
        if pos + 28 >= len(panel.daily.columns):
            raise ValueError("Incomplete 28-day target")
        frame["target"] = panel.daily.loc[skus, future_dates].sum(axis=1).to_numpy()
    return frame


def scoring_frame(panel, frame, origin):
    """Score unseen future-selling SKU as zero forecast without exposing them to predictor."""
    dates = pd.date_range(pd.Timestamp(origin) + pd.Timedelta(days=1), periods=28)
    if not dates.isin(panel.daily.columns).all():
        raise ValueError("Incomplete target")
    actual = panel.daily.loc[:, dates].sum(axis=1)
    unseen = actual.loc[~actual.index.isin(frame.sku) & actual.gt(0)]
    return unseen
