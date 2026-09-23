"""Safety properties of temporal forecasting, target semantics and persisted inference."""
import copy
from pathlib import Path
import tempfile
import unittest
import numpy as np
import pandas as pd
from openpyxl import Workbook
from .data import Panel, load_panels
from .features import features_at, monthly_projection, scoring_frame
from .methods import (adaptive_predictions, fit_model, predict_model, simple_predictions,
                      save_model, load_model)
from .run import metrics, score_rows


class TemporalTests(unittest.TestCase):
    def setUp(self):
        dates = pd.date_range('2025-01-01', '2026-09-22')
        rng = np.random.default_rng(43)
        daily = pd.DataFrame(rng.poisson(3, (3, len(dates))), index=['001_', '002_', 'future'], columns=dates).astype(float)
        daily.loc['future', :'2026-05-04'] = 0
        monthly = pd.DataFrame(np.tile(np.arange(1, 13)*100., (3, 1)), index=daily.index,
                               columns=pd.date_range('2024-01-01', periods=12, freq='MS'))
        self.panel = Panel('SE__pieces', 'SE', 'шт', 'Алматы', daily, monthly, {})
        self.origin = pd.Timestamp('2026-05-04')

    def test_future_sales_cannot_change_features_or_candidates(self):
        before = features_at(self.panel, self.origin, True)
        simple = simple_predictions(self.panel, before)
        changed = copy.deepcopy(self.panel)
        changed.daily.loc[:, self.origin+pd.Timedelta(days=1):] += 100000
        after = features_at(changed, self.origin, True)
        pd.testing.assert_frame_equal(before.drop(columns='target'), after.drop(columns='target'))
        pd.testing.assert_frame_equal(simple, simple_predictions(changed, after))
        np.testing.assert_allclose(after.target-before.target, 2800000)

    def test_exact_target_and_no_clipping(self):
        self.panel.daily.loc[:, :] = 1
        self.panel.daily.loc[:, self.origin] = 100000
        self.panel.daily.loc[:, self.origin+pd.Timedelta(days=29)] = 900000
        self.panel.daily.loc[:, self.origin+pd.Timedelta(days=2)] = 1000000
        frame = features_at(self.panel, self.origin, True)
        np.testing.assert_allclose(frame.target, 1000027)
        self.assertTrue((frame.label_end == self.origin+pd.Timedelta(days=28)).all())

    def test_2024_is_context_only_not_daily_truth(self):
        before = features_at(self.panel, self.origin, True)
        daily_copy = self.panel.daily.copy()
        self.panel.monthly_2024 *= 9
        after = features_at(self.panel, self.origin, True)
        np.testing.assert_allclose(before.target, after.target)
        np.testing.assert_allclose(before.rate364, after.rate364)
        np.testing.assert_allclose(before.monthly_2year28*9, after.monthly_2year28)
        pd.testing.assert_frame_equal(daily_copy, self.panel.daily)
        without = features_at(self.panel, self.origin, True, history2024=False)
        self.assertTrue(without.monthly_2year28.isna().all())

    def test_unseen_not_exposed_but_scored(self):
        frame = features_at(self.panel, self.origin, True)
        self.assertNotIn('future', frame.sku.tolist())
        unseen = scoring_frame(self.panel, frame, self.origin)
        self.assertEqual(list(unseen.index), ['future'])
        result = score_rows(self.panel, frame, {'selected': np.zeros(len(frame))})
        self.assertAlmostEqual(result.target.sum(), self.panel.daily.loc[:, '2026-05-05':'2026-06-01'].sum().sum())
        self.assertEqual(result.iloc[-1]['group'], 'unseen')
        self.assertEqual(result.iloc[-1]['selected'], 0)

    def test_partial_target_and_short_history_rejected(self):
        with self.assertRaises(ValueError):
            features_at(self.panel, '2025-02-01')
        with self.assertRaises(ValueError):
            features_at(self.panel, '2026-09-01', True)

    def test_aggregate_month_projection_marks_missing(self):
        table = pd.DataFrame({pd.Timestamp('2024-05-01'): [310., np.nan],
                              pd.Timestamp('2024-06-01'): [600., 90.]})
        projected = monthly_projection(table, pd.date_range('2026-05-20', periods=28), 2024)
        self.assertEqual(projected[0], 12*10+16*20)
        self.assertTrue(np.isnan(projected[1]))

    def test_train_cutoff_future_labels_and_weight_roundtrip(self):
        origins = pd.date_range('2025-08-01', '2026-06-01', freq='28D')
        hist = pd.concat([features_at(self.panel, x, True) for x in origins], ignore_index=True)
        current = features_at(self.panel, self.origin)
        for name in ('ridge_1', 'cb_mae_d4'):
            model, meta = fit_model(name, hist, self.origin, iterations=8, threads=1)
            self.assertLessEqual(pd.Timestamp(meta['label_end_max']), self.origin)
            changed = hist.copy()
            changed.loc[changed.label_end > self.origin, 'target'] = 1e9
            other, _ = fit_model(name, changed, self.origin, iterations=8, threads=1)
            expected = predict_model(name, model, current)
            np.testing.assert_allclose(expected, predict_model(name, other, current), rtol=1e-10)
            with tempfile.TemporaryDirectory() as tmp:
                path = save_model(name, model, Path(tmp)/'weights')
                np.testing.assert_allclose(expected, predict_model(name, load_model(name, path), current), rtol=1e-12)

    def test_adaptive_selector_never_uses_unfinished_outcomes(self):
        origins = pd.date_range('2025-08-01', '2026-06-01', freq='28D')
        frames = [features_at(self.panel, x, True) for x in origins]
        hist = pd.concat(frames, ignore_index=True)
        preds = pd.concat([simple_predictions(self.panel, f) for f in frames], ignore_index=True)
        current = features_at(self.panel, self.origin)
        simple = simple_predictions(self.panel, current)
        before = adaptive_predictions(current, simple, hist, preds)
        changed = hist.copy()
        changed.loc[changed.label_end > self.origin, 'target'] = 1e9
        preds.loc[changed.label_end > self.origin, :] = 2e9
        after = adaptive_predictions(current, simple, changed, preds)
        for name in before:
            np.testing.assert_array_equal(before[name], after[name])

    def test_zero_denominator_and_pooled_metrics(self):
        zero = metrics([0, 0], [2, 3])
        self.assertIsNone(zero['wape'])
        self.assertEqual(zero['absolute_error_on_zero_actual'], 5)
        pooled = metrics([1, 100], [2, 110])
        self.assertAlmostEqual(pooled['wape'], 11/101)
        self.assertEqual(pooled['mae'], 5.5)


class SourceTests(unittest.TestCase):
    def test_overlap_net_sales_not_added_and_summary_not_invoice(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)/'Systeme electric'
            folder.mkdir()
            wb = Workbook(); s = wb.active
            s.append(['Дата', 'Номер', 'Документ', 'Код', 'Номенклатура', 'Ед.', 'Склад', 'Количество'])
            s.append(['01.01.2025', '1', 'Расходная накладная', '001_', 'x', 'шт', 'Алматы', 4])
            s.append(['01.01.2025', '2', 'Расходная накладная', '001_', 'x', 'шт', 'Алматы', -2])
            s.append(['22.09.2026', '3', 'Расходная накладная', '001_', 'x', 'шт', 'Алматы', 6])
            s.append([None, None, None, None, None, None, None, 100000])
            wb.save(folder/'Динамика.xlsx')
            wb = Workbook(); s = wb.active
            headers = [f'{m} 2024' for m in ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']]
            s.append(['Номенклатура', 'Номенклатура.Код']+headers+['янв 2025'])
            s.append(['x', '001_']+[-2.]+[100.]*11+[999999.])
            wb.save(folder/'Ежемесячные продажи.xlsx')
            panels, audit = load_panels(tmp, ('SE',))
            self.assertEqual(panels[0].daily.to_numpy().sum(), 10)
            self.assertEqual(panels[0].daily.loc['001_', '2025-01-01'], 4)
            self.assertEqual(panels[0].monthly_2024.iloc[0, 0], -2)
            self.assertTrue((panels[0].monthly_2024.columns.year == 2024).all())
            self.assertEqual(audit['SE']['transactions']['used_positive_invoice_rows'], 2)


class EnsembleTests(unittest.TestCase):
    def test_saved_pair_uses_exact_validation_weight(self):
        from .run import forecast_methods
        case = TemporalTests()
        case.setUp()
        panel, origin = case.panel, case.origin
        frame = features_at(panel, origin)
        hist = pd.concat([features_at(panel, date, True) for date in pd.date_range('2025-08-01', '2026-03-01', freq='28D')], ignore_index=True)
        model, _ = fit_model('ridge_1', hist, origin)
        pair = 'mix|ridge_1|mean364|0.25'
        result, fits = forecast_methods(panel, frame, [pair], hist, pd.DataFrame(), 1, 1, {'ridge_1': model})
        expected = .25*predict_model('ridge_1', model, frame) + .75*frame.rate364.to_numpy()
        np.testing.assert_allclose(result[pair], expected)
        self.assertEqual(fits, [])


if __name__ == '__main__':
    unittest.main()
