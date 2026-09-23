"""Generate explicitly SYNTHETIC XLSX inputs; no partner data or model retraining.

Run from the repository root:
    .venv-ml/Scripts/python.exe scripts/generate_sample_data.py --output data/samples

The dated fixture exercises saved forecast-v2 weights, supplemental demand
correction and a fully specified procurement decision. It measures functionality,
not out-of-sample accuracy. Generated workbooks must stay outside Git.
"""
import argparse
from datetime import date, datetime, timedelta
import json
import math
from pathlib import Path

from openpyxl import Workbook


AS_OF = date(2026, 9, 22)
MONTHS = ("янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")
PRODUCTS = (
    ("SYN-REG-001", "SYNTHETIC · Регулярный товар", 4, 30, 10),
    ("SYN-OUT-002", "SYNTHETIC · Товар с отсутствием", 8, 20, 10),
    ("SYN-ONE-003", "SYNTHETIC · Разовая крупная покупка", 3, 15, 5),
    ("SYN-SEASON-004", "SYNTHETIC · Сезонный товар", 5, 25, 5),
)


def _workbook(path, rows):
    book = Workbook()
    book.active.title = "TDSheet"
    for row in rows:
        book.active.append(row)
    book.save(path)
    book.close()


def generate(output):
    """Return paths for six Excel roles, context JSON and calculation parameters."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    names = {
        "sales_transactions": "Динамика SYNTHETIC.xlsx",
        "sales_monthly": "Ежемесячные продажи SYNTHETIC.xlsx",
        "stock_monthly": "Ежемесячные остатки SYNTHETIC.xlsx",
        "seasonality": "Сезонность SYNTHETIC.xlsx",
        "moq": "MOQ SYNTHETIC.xlsx",
        "current_stock_inbound": "Товар в пути SYNTHETIC на 22.09.2026.xlsx",
    }
    paths = {role: output / filename for role, filename in names.items()}
    transactions = [["Дата", "Номер", "Документ", "Код", "Номенклатура", "Ед.", "Склад", "Количество"]]
    totals, labels = {}, []
    start = date(2025, 1, 1)
    stockout_start, stockout_end = AS_OF - timedelta(days=18), AS_OF - timedelta(days=5)
    oneoff_date = AS_OF - timedelta(days=10)
    for day in range((AS_OF - start).days + 1):
        stamp = start + timedelta(days=day)
        for sku, name, rate, _, _ in PRODUCTS:
            if sku == "SYN-OUT-002" and stockout_start <= stamp <= stockout_end:
                continue
            quantity = rate if stamp.weekday() < 5 else max(1, rate // 2)
            if sku == "SYN-SEASON-004":
                quantity = max(1, round(rate * (1 + .35 * math.sin(2 * math.pi * day / 365.25)) * (1 + day / 1200)))
            stamp_dt = datetime.combine(stamp, datetime.min.time())
            document = f"SYN-{day}-{sku}"
            transactions.append([stamp_dt, document, "Расходная накладная " + document,
                                 sku, name, "шт", "Алматы", quantity])
            if sku == "SYN-ONE-003":
                labels.append({"source_event_id": f"sales_transactions:TDSheet:{len(transactions)}",
                               "pseudonymous_client_id": "synthetic-regular-client"})
            month_key = (sku, stamp.year, stamp.month)
            totals[month_key] = totals.get(month_key, 0) + quantity
            if sku == "SYN-ONE-003" and stamp == oneoff_date:
                transactions.append([stamp_dt, "SYN-PROJECT", "Расходная накладная SYN-PROJECT",
                                     sku, name, "шт", "Алматы", 300])
                labels.append({"source_event_id": f"sales_transactions:TDSheet:{len(transactions)}",
                               "pseudonymous_client_id": "synthetic-oneoff-client"})
                totals[month_key] += 300
    _workbook(paths["sales_transactions"], transactions)
    months = [(year, month) for year in (2024, 2025, 2026) for month in range(1, 13)
              if date(year, month, 1) <= AS_OF]
    month_headers = [f"{MONTHS[month - 1]}. {year}" for year, month in months]
    monthly = [["Номенклатура", "Номенклатура.Код", "Артикул", "Кратность", *month_headers]]
    stocks = [["№", "Номенклатура", "Номенклатура.Код", "Ед.изм", *month_headers]]
    moq = [["№", "Номенклатура", "Номенклатура.Код", "Артикул", "Кратность"]]
    headers = [None] * 57
    for position, label in {1: "Артикул", 2: "Код 1с", 3: "Номенклатура", 4: "Категория 2026",
                            49: "Остаток", 50: "Зарезервировано", 51: "Свободный остаток",
                            54: "СЭ в пути 24.09", 55: "Минимальная партия", 56: "Склад"}.items():
        headers[position] = label
    current = [["SYNTHETIC — учебный снимок, не партнёрские данные"], headers]
    for index, (sku, name, rate, stock, multiple) in enumerate(PRODUCTS, 1):
        quantities = [totals.get((sku, year, month), rate * 25) for year, month in months]
        monthly.append([name, sku, "ART-" + sku, multiple, *quantities])
        stocks.append([index, name, sku, "шт", *([stock] * len(months))])
        moq.append([index, name, sku, "ART-" + sku, multiple])
        row = [None] * len(headers)
        for position, value in {1: "ART-" + sku, 2: sku, 3: name, 4: "Учебная категория",
                                49: stock, 50: 0, 51: stock, 54: 10,
                                55: multiple, 56: "almaty"}.items():
            row[position] = value
        current.append(row)
    _workbook(paths["sales_monthly"], monthly)
    _workbook(paths["stock_monthly"], stocks)
    _workbook(paths["moq"], moq)
    _workbook(paths["current_stock_inbound"], current)
    _workbook(paths["seasonality"], [["год", *range(1, 13)],
                                    *[[year, *[1000 + month * 20 for month in range(12)]] for year in (2024, 2025, 2026)]])
    context = {
        "client_labels": labels,
        "stockout_intervals": [{"sku": "SYN-OUT-002", "warehouse_id": "almaty",
                                "start_date": str(stockout_start), "end_date": str(stockout_end),
                                "source_kind": "synthetic", "reference": "generated-full-day-stockout"}],
        "price_observations": [{"sku": sku, "effective_date": str(AS_OF), "selling_price": 1500,
                                "unit_cost": 1000, "variable_selling_cost": 0, "source_kind": "synthetic",
                                "reference": "generated-example-price"} for sku, *_ in PRODUCTS],
        "material_requirements": [],
    }
    request = {"dataset_id": "REPLACE_WITH_UPLOADED_DATASET_ID", "as_of_date": str(AS_OF),
               "warehouse_ids": ["almaty"], "category_codes": [], "horizon_days": 28,
               "lead_time_days": 7, "review_period_days": 21, "mode": "scenario", "budget_kzt": None,
               "request_ai_review": False, "category_policies": [{"category_raw": "Учебная категория",
                   "target_quantile": .9, "minimum_target_quantile": None, "source_kind": "manual",
                   "rationale": "Учебная политика; единственная траектория, квантили не калиброваны"}],
               "economic_profiles": [], "growth_adjustments": []}
    paths["context"] = output / "additional-context.SYNTHETIC.json"
    paths["request"] = output / "calculation-request.SYNTHETIC.json"
    for key, payload in (("context", context), ("request", request)):
        paths[key].write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return paths


def generate_iek(output):
    """Three distinct physical units plus explicit current stock; all synthetic."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    products = (("SYN-IEK-PCS", "шт", 6, 1, 5), ("SYN-IEK-M", "м", 12.5, .5, 5),
                ("SYN-IEK-PACK", "упак", 2, 1, 1))
    paths = {
        "sales_transactions": output / "Динамика IEK SYNTHETIC.xlsx",
        "sales_monthly": output / "Ежемесячные продажи IEK SYNTHETIC.xlsx",
        "current_stock_inbound": output / "current_stock_inbound IEK SYNTHETIC.xlsx",
        "context": output / "additional-context.SYNTHETIC.json",
        "request": output / "calculation-request.SYNTHETIC.json",
    }
    sales = [["Дата", "Номер", "Документ", "Код", "Номенклатура", "Ед.", "Склад", "Количество"]]
    start = date(2025, 1, 1)
    for day in range((AS_OF - start).days + 1):
        stamp = datetime.combine(start + timedelta(days=day), datetime.min.time())
        for sku, unit, quantity, _, _ in products:
            identifier = f"SYN-{sku}-{day}"
            sales.append([stamp, identifier, "Расходная накладная " + identifier,
                          sku, "SYNTHETIC · IEK " + unit, unit, "Алматы", quantity])
    monthly = [["Номенклатура", "Номенклатура.Код", *[f"{month}. 2024" for month in MONTHS]]]
    inventory = [["Код", "Номенклатура", "Ед.", "Склад", "Дата снимка", "Остаток", "Зарезервировано",
                  "Свободный остаток", "Минимальная партия", "Кратность", "Шаг единицы", "Категория",
                  "Цена закупки KZT", "В пути", "Дата поступления"]]
    for sku, unit, quantity, quantum, multiple in products:
        name = "SYNTHETIC · IEK " + unit
        monthly.append([name, sku, *([quantity * 30] * 12)])
        inventory.append([sku, name, unit, "almaty", str(AS_OF), 10, 0, 10, multiple, multiple,
                          quantum, "Учебная IEK", 500, 5, str(AS_OF + timedelta(days=2))])
    for role, rows in (("sales_transactions", sales), ("sales_monthly", monthly), ("current_stock_inbound", inventory)):
        _workbook(paths[role], rows)
    request = {"dataset_id": "REPLACE_WITH_UPLOADED_DATASET_ID", "as_of_date": str(AS_OF),
               "warehouse_ids": ["almaty"], "category_codes": [], "horizon_days": 28,
               "lead_time_days": 7, "review_period_days": 21, "mode": "scenario", "budget_kzt": None,
               "request_ai_review": False, "category_policies": [{"category_raw": "Учебная IEK",
                   "target_quantile": .9, "minimum_target_quantile": None, "source_kind": "manual",
                   "rationale": "Явная учебная политика IEK; квантили не калиброваны"}],
               "economic_profiles": [], "growth_adjustments": []}
    paths["context"].write_text("{}\n", encoding="utf-8")
    paths["request"].write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return paths


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/samples/SYNTHETIC"))
    parser.add_argument("--supplier", choices=("systeme-electric", "iek"), default="systeme-electric")
    arguments = parser.parse_args()
    generated = (generate_iek if arguments.supplier == "iek" else generate)(arguments.output)
    print("SYNTHETIC sample only; not partner data or forecast accuracy evidence.")
    for role, path in generated.items():
        print(f"{role}: {path.resolve()}")
