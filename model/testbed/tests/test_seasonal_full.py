"""Independent deterministic controls; no generator/final-seed tuning."""
from copy import deepcopy
from datetime import date, timedelta
import math
import unittest
from unittest.mock import patch

from model.testbed.adapters import validate_request, validate_prediction
from model.testbed import seasonal


def control(days=560, quantity=10., cutoff=date(2028, 7, 15)):
    history = [{'date': (cutoff-timedelta(days=d)).isoformat(), 'observed_quantity': quantity,
                'availability_fraction': 1., 'complete': True} for d in range(days-1, -1, -1)]
    item = {'sku': 'independent-control', 'unit': 'шт', 'warehouse_id': 'control',
            'supplier_id': 'control', 'category_raw': 'control',
            'launch_date': (cutoff-timedelta(days=max(days-1, 0))).isoformat(),
            'history': history, 'events': [], 'known_promotions': [], 'analogue_history': []}
    return {'schema_version': 'forecast-input-v2', 'as_of': cutoff.isoformat(), 'horizon_days': 28, 'items': [item]}


def promo(cutoff, start, end, multiplier, announced=-200):
    return {'start_date': (cutoff+timedelta(days=start)).isoformat(),
            'end_date': (cutoff+timedelta(days=end)).isoformat(),
            'announced_at': (cutoff+timedelta(days=announced)).isoformat(),
            'planned_multiplier': multiplier, 'source': 'independent-control'}


class SeasonalFullTests(unittest.TestCase):
    def prediction(self, request):
        output = seasonal.forecast(request)
        validate_prediction(request, output)
        return output[request['items'][0]['sku']]

    def test_no_observed_support_is_not_zero_demand(self):
        for kind in ('all_stockout', 'all_incomplete', 'cold_start'):
            with self.subTest(kind=kind):
                request = control()
                item = request['items'][0]
                if kind == 'cold_start':
                    item['history'] = []
                    item['launch_date'] = request['as_of']
                else:
                    for row in item['history']:
                        row['observed_quantity'] = 0
                        if kind == 'all_stockout':
                            row['availability_fraction'] = 0
                        else:
                            row['complete'] = False
                validate_request(request)
                with self.assertRaises(seasonal.InsufficientHistory):
                    self.prediction(request)

    def test_explicit_recent_analogue_supports_full_stockout_and_cold_start(self):
        for cold in (False, True):
            request = control()
            item = request['items'][0]
            for row in item['history']:
                row.update(observed_quantity=0, availability_fraction=0)
            if cold:
                item['history'] = []
                item['launch_date'] = request['as_of']
            item['analogue_history'] = [{'date': request['as_of'], 'quantity': 17., 'source': 'provided-control-analogue'}]
            self.assertEqual(self.prediction(request), [17.]*28)

    def test_short_stockout_uses_older_supported_window(self):
        request = control()
        for row in request['items'][0]['history'][-56:]:
            row.update(observed_quantity=0, availability_fraction=0)
        self.assertEqual(self.prediction(request), [10.]*28)

    def test_stale_history_and_stale_analogue_fail_explicitly(self):
        request = control()
        cutoff = date.fromisoformat(request['as_of'])
        item = request['items'][0]
        item['history'] = [r for r in item['history'] if r['date'] < (cutoff-timedelta(days=168)).isoformat()]
        item['analogue_history'] = [{'date': (cutoff-timedelta(days=169)).isoformat(), 'quantity': 20., 'source': 'stale'}]
        with self.assertRaises(seasonal.InsufficientHistory):
            self.prediction(request)

    def test_true_observed_zero_is_valid_but_unknown_exposure_is_rejected(self):
        request = control(quantity=0)
        self.assertEqual(self.prediction(request), [0.]*28)
        request['items'][0]['history'][-1]['availability_fraction'] = None
        with self.assertRaises(ValueError):
            self.prediction(request)

    def test_extreme_valid_values_fail_instead_of_nonfinite_predictions(self):
        controls = []
        request = control(days=28, quantity=1e308)
        controls.append(request)
        request = control(days=2, quantity=1e307)  # Valid daily fit; invalid 28-day sum.
        controls.append(request)
        request = control(days=28, quantity=1)
        for row in request['items'][0]['history']:
            row['availability_fraction'] = 1e-320
        controls.append(request)
        for request in controls:
            validate_request(request)
            with self.subTest(quantity=request['items'][0]['history'][0]['observed_quantity']):
                with self.assertRaises(ValueError):
                    self.prediction(request)

    def test_overlapping_promotion_overflow_and_underflow_are_explicit(self):
        for multiplier in (1e308, 1e-300):
            request = control(days=56)
            cutoff = date.fromisoformat(request['as_of'])
            request['items'][0]['known_promotions'] = [promo(cutoff, 1, 28, multiplier, announced=0)]*2
            validate_request(request)
            with self.subTest(multiplier=multiplier), self.assertRaises(ValueError):
                self.prediction(request)

    def test_large_representable_quantity_remains_accurate(self):
        request = control(days=56, quantity=1e100)
        predicted = self.prediction(request)
        self.assertTrue(all(math.isclose(p, 1e100, rel_tol=1e-12) for p in predicted))

    def test_exponential_overflow_is_not_silently_clipped(self):
        request = control()
        item = request['items'][0]
        with patch.object(seasonal, 'solve', return_value=[1000., 0, 0, 0, 0, 0]):
            with self.assertRaises(ValueError) as error:
                seasonal.fitted(item, date.fromisoformat(request['as_of']), 'seasonal_trend')
            self.assertIn('exponential', str(error.exception.__cause__))

    def test_two_regular_weekly_large_purchases_are_not_erased(self):
        request = control()
        item = request['items'][0]
        for index in (-16, -9):
            row = item['history'][index]
            row['observed_quantity'] += 1000
            item['events'].append({'date': row['date'], 'quantity': 1000., 'client_id': 'regular-client', 'event_id': str(index)})
        self.assertEqual(math.fsum(r[1] for r in seasonal.observations(item)), 5600+2000)
        changed = deepcopy(item)
        changed['events'].reverse()
        changed['history'].reverse()
        self.assertEqual(seasonal.observations(item), seasonal.observations(changed))

    def test_single_project_episode_and_sparse_sales_remain_distinct(self):
        request = control()
        item = request['items'][0]
        row = item['history'][-10]
        row['observed_quantity'] += 1000
        item['events'] = [{'date': row['date'], 'quantity': 1000., 'client_id': 'one-episode', 'event_id': 'one'}]
        self.assertEqual(math.fsum(r[1] for r in seasonal.observations(item)), 5600)
        request = control(quantity=0)
        item = request['items'][0]
        item['history'][-10]['observed_quantity'] = 1000
        item['events'] = [{'date': item['history'][-10]['date'], 'quantity': 1000., 'client_id': 'rare', 'event_id': 'rare'}]
        self.assertEqual(math.fsum(r[1] for r in seasonal.observations(item)), 1000)
        self.assertGreater(sum(self.prediction(request)), 0)

    def test_known_promotion_normalization_does_not_flag_a_regular_purchase(self):
        request = control()
        item = request['items'][0]
        cutoff = date.fromisoformat(request['as_of'])
        item['known_promotions'] = [promo(cutoff, -10, -10, 100.)]
        row = item['history'][-11]
        row['observed_quantity'] = 1000
        item['events'] = [{'date': row['date'], 'quantity': 1000., 'client_id': 'promoted-regular', 'event_id': 'promo'}]
        self.assertEqual(math.fsum(r[1] for r in seasonal.observations(item)), 5600)

    def test_combined_promotions_recover_level_and_apply_future_factors(self):
        request = control()
        item = request['items'][0]
        cutoff = date.fromisoformat(request['as_of'])
        item['known_promotions'] = [promo(cutoff, -100, 28, 2.), promo(cutoff, -50, 28, 3.)]
        for row in item['history']:
            row['observed_quantity'] *= seasonal.promotion(item, row['date'])
        self.assertEqual(self.prediction(request), [60.]*28)

    def test_future_observations_and_announcements_are_not_silently_accepted(self):
        request = control()
        cutoff = date.fromisoformat(request['as_of'])
        request['items'][0]['known_promotions'] = [promo(cutoff, 2, 28, 2., announced=1)]
        with self.assertRaises(ValueError):
            self.prediction(request)
        request = control()
        request['items'][0]['history'][-1]['date'] = (cutoff+timedelta(days=1)).isoformat()
        with self.assertRaises(ValueError):
            self.prediction(request)

    def test_selection_is_past_only_and_preserves_raw_observed_targets(self):
        request = control()
        item = request['items'][0]
        cutoff = date.fromisoformat(request['as_of'])
        item['history'][-10]['observed_quantity'] += 1000
        item['events'] = [{'date': item['history'][-10]['date'], 'quantity': 1000., 'client_id': 'project', 'event_id': 'project'}]
        item['known_promotions'] = [promo(cutoff, 1, 28, 2., announced=0)]
        origins = []
        def fitted(train, origin, method):
            origins.append(origin)
            self.assertTrue(all(r['date'] <= origin.isoformat() for r in train['history']))
            self.assertTrue(all(p['announced_at'] <= origin.isoformat() for p in train['known_promotions']))
            return lambda day: {'mean56': 10., 'mean168': 20., 'seasonal_trend': 30.}[method]
        with patch.object(seasonal, 'fitted', side_effect=fitted):
            method, scores, windows = seasonal.select(item, cutoff)
        self.assertEqual(windows, 3)
        self.assertEqual(scores, {'mean56': 1000., 'mean168': 1280., 'seasonal_trend': 1560.})
        self.assertEqual(method, 'mean56')
        self.assertEqual(set(origins), {cutoff-timedelta(days=d) for d in (84, 56, 28)})

    def test_deterministic_growth_and_order_invariance(self):
        request = control()
        cutoff = date.fromisoformat(request['as_of'])
        item = request['items'][0]
        for row in item['history']:
            row['observed_quantity'] = 10*math.exp(.002*(date.fromisoformat(row['date'])-cutoff).days)
        predicted = self.prediction(request)
        truth = [10*math.exp(.002*d) for d in range(1, 29)]
        self.assertLess(abs(math.fsum(predicted)-math.fsum(truth))/math.fsum(truth), .02)
        self.assertGreater(predicted[-1], predicted[0])
        changed = deepcopy(request)
        changed['items'][0]['history'].reverse()
        self.assertEqual(predicted, self.prediction(changed))
        self.assertEqual(request['items'][0]['history'][0]['date'], item['launch_date'])


if __name__ == '__main__':
    unittest.main()
