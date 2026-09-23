from copy import deepcopy
from datetime import date,timedelta
import re
import unittest
from unittest.mock import patch

from model.testbed.generator import generate
from model.testbed.requirement_checks import deterministic_request
from model.testbed.seasonal import forecast,observations,select,at_cutoff


class RequirementCorrections(unittest.TestCase):
    def test_opaque_ids_invariant_to_class_with_exact_public_ledger(self):
        for scenario in ('stable','single_project','split_project','recurring_client'):
            case=generate(scenario,101,'2026-04-01')
            for item in case.observed['items']:
                ids=[e['event_id'] for e in item['events']]
                self.assertEqual(len(ids),len(set(ids)))
                self.assertTrue(all(re.fullmatch(r'event-[0-9a-f]{32}',x) for x in ids))
                for row in item['history']:
                    self.assertEqual(row['observed_quantity'],sum(e['quantity'] for e in item['events'] if e['date']==row['date']))

    def test_season_and_growth_controls(self):
        for mode in ('seasonal','growth'):
            request,truth=deterministic_request(mode)
            predicted=forecast(request)['control']
            self.assertLess(abs(sum(predicted)-sum(truth))/sum(truth),.1)
            self.assertGreater(max(predicted)-min(predicted),.1)

    def test_sparse_purchase_is_not_deleted_as_project(self):
        request,_=deterministic_request('stable');item=request['items'][0]
        for row in item['history']:row['observed_quantity']=0.
        row=item['history'][-20];row['observed_quantity']=100.
        item['events']=[{'date':row['date'],'quantity':100.,'client_id':'sparse-customer','event_id':'opaque'}]
        self.assertEqual(sum(r[1] for r in observations(item)),100.)
        self.assertGreater(sum(forecast(request)['control']),0)

    def test_input_order_and_identifier_do_not_change_direct_prediction(self):
        request,_=deterministic_request();expected=forecast(request)
        request['items'][0]['history'].reverse()
        request['items'][0]['sku']='opaque-replacement'
        self.assertEqual(expected['control'],forecast(request)['opaque-replacement'])

    def test_each_inner_fit_uses_only_that_origin_information(self):
        request,_=deterministic_request();item=request['items'][0];cutoff=date.fromisoformat(request['as_of'])
        item['known_promotions']=[{'announced_at':cutoff.isoformat(),'start_date':(cutoff+timedelta(days=1)).isoformat(),
                                  'end_date':(cutoff+timedelta(days=7)).isoformat(),'planned_multiplier':2,'source':'control'}]
        import model.testbed.seasonal as seasonal
        real=seasonal.fitted
        origins=[]
        def checked(train,origin,method):
            self.assertTrue(all(r['date']<=origin.isoformat() for r in train['history']))
            self.assertTrue(all(p['announced_at']<=origin.isoformat() for p in train['known_promotions']))
            origins.append(origin)
            return real(train,origin,method)
        with patch.object(seasonal,'fitted',side_effect=checked):select(item,cutoff)
        self.assertEqual(set(origins),{cutoff-timedelta(days=x) for x in (84,56,28)})
        before=at_cutoff(item,cutoff-timedelta(days=28))
        changed=deepcopy(item);changed['history'][-1]['observed_quantity']=1e9
        self.assertEqual(before,at_cutoff(changed,cutoff-timedelta(days=28)))


if __name__=='__main__':unittest.main()
