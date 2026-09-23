"""Import local partner XLSX through the real HTTP API; never copy them into Git."""
import argparse
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
import time
from uuid import uuid4

import httpx


def wait(client, initial):
    job = initial
    deadline = time.monotonic() + 600
    last_stage = None
    while job['status'] not in ('succeeded', 'failed'):
        if time.monotonic() > deadline:
            raise RuntimeError('Job is still running; inspect /jobs/' + job['job_id'])
        if job['stage'] != last_stage:
            print(job['stage'], flush=True)
            last_stage = job['stage']
        time.sleep(1)
        response = client.get('/jobs/' + job['job_id'])
        response.raise_for_status()
        job = response.json()
    if job['status'] == 'failed':
        raise RuntimeError(job['error']['error']['message'])
    return job['resource_id']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--supplier', required=True, choices=['iek', 'systeme-electric'])
    parser.add_argument('--api', default='http://127.0.0.1:8000/api/v1')
    parser.add_argument('--forecast', action='store_true')
    args = parser.parse_args()
    files = sorted(args.directory.glob('*.xlsx'))
    if not 1 <= len(files) <= 6:
        parser.error('The directory must contain 1-6 XLSX files.')
    with httpx.Client(base_url=args.api, timeout=60) as client, ExitStack() as stack:
        uploaded = client.post('/datasets/import', headers={'Idempotency-Key': str(uuid4())},
                               data={'supplier_id': args.supplier}, files=[
            ('files', (path.name, stack.enter_context(path.open('rb')),
                       'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')) for path in files])
        uploaded.raise_for_status()
        dataset_id = wait(client, uploaded.json())
        dataset = client.get('/datasets/' + dataset_id).json()
        print(f"Dataset: {dataset_id}; SKUs: {dataset['sku_count']}; data_as_of: {dataset['data_as_of']}", flush=True)
        if not args.forecast:
            return
        if not dataset['calculation_allowed']:
            raise RuntimeError('Dataset requires additional sources before forecasting.')
        response = client.post('/calculations', headers={'Idempotency-Key': str(uuid4())}, json={
            'dataset_id': dataset_id, 'as_of_date': dataset['data_as_of'], 'warehouse_ids': ['almaty'],
            'category_codes': [], 'horizon_days': 28, 'lead_time_days': 7, 'review_period_days': 21,
            'mode': 'operational', 'category_policies': [], 'economic_profiles': [],
            'growth_adjustments': [], 'budget_kzt': None, 'request_ai_review': False})
        response.raise_for_status()
        calculation_id = wait(client, response.json())
        result = client.get('/calculations/' + calculation_id + '/recommendations').json()
        print(f"Calculation: {calculation_id}; items: {len(result['items'])}", flush=True)
        print('SKU counts by unit:', dict(Counter(item['unit'] for item in result['items'])), flush=True)
        print('Open http://127.0.0.1:5173 and reload the page.', flush=True)


if __name__ == '__main__':
    main()
