"""Checks against temporal leakage and horizon mistakes in training features."""
import unittest
import numpy as np
import pandas as pd
from pipeline import FEATURES, features_at


class TemporalTests(unittest.TestCase):
    def setUp(self):
        self.dates = pd.date_range("2025-01-01", periods=450)
        self.daily = pd.DataFrame(np.ones((2, 450)), index=["0001", "0002"], columns=self.dates)

    def test_future_does_not_change_features(self):
        origin = self.dates[390]
        before = features_at(self.daily, origin, True)
        mutated = self.daily.copy()
        mutated.loc[:, origin + pd.Timedelta(days=1):] *= 1000
        after = features_at(mutated, origin, True)
        pd.testing.assert_frame_equal(before[FEATURES], after[FEATURES])
        self.assertTrue((after["target"] == before["target"] * 1000).all())

    def test_target_excludes_origin_and_has_exactly_28_days(self):
        origin = self.dates[100]
        self.daily.loc[:, origin] = 10000
        self.daily.loc[:, origin + pd.Timedelta(days=29)] = 10000
        row = features_at(self.daily, origin, True).iloc[0]
        self.assertEqual(row["target"], 28)
        self.assertEqual(row["sum7"], 10006)
        self.assertEqual(row["label_end"], origin + pd.Timedelta(days=28))

    def test_future_sku_is_not_exposed_at_earlier_cutoff(self):
        self.daily.loc["0002", :] = 0
        self.daily.loc["0002", self.dates[101]] = 20
        result = features_at(self.daily, self.dates[100], True)
        self.assertEqual(result["sku"].tolist(), ["0001"])

    def test_short_or_incomplete_windows_fail(self):
        with self.assertRaises(ValueError):
            features_at(self.daily, self.dates[50])
        with self.assertRaises(ValueError):
            features_at(self.daily, self.dates[-10], True)

    def test_same_period_previous_year_is_in_the_past(self):
        row = features_at(self.daily, self.dates[390], True).iloc[0]
        self.assertEqual(row["prior_year28"], 28)
        self.assertEqual(row["seasonal_available"], 1)


if __name__ == "__main__":
    unittest.main()
