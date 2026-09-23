"""Import local partner XLSX through the real HTTP API; never copy them into Git."""
import argparse
import getpass
from collections import Counter
from contextlib import ExitStack
from ipaddress import ip_address
from pathlib import Path
import time
import warnings
from uuid import uuid4

import httpx


def validate_api_url(value):
    """Reject embedded credentials and ambiguous URL components before any request."""
    try:
        url = httpx.URL(value)
    except (httpx.InvalidURL, ValueError):
        raise RuntimeError('API URL is invalid. Use an HTTP(S) URL ending in /api/v1.') from None
    if (url.scheme not in ('http', 'https') or not url.host or url.username or url.password
            or url.query or url.fragment):
        raise RuntimeError('API URL must be HTTP(S), without credentials, query or fragment.')
    return url


def safe_for_credentials(url):
    if url.scheme == 'https':
        return True
    if url.host.lower() == 'localhost':
        return True
    try:
        return ip_address(url.host).is_loopback
    except ValueError:
        return False


def authenticate(client, email=None, password_prompt=None):
    """Keep session cookies in this client and attach CSRF to subsequent mutations."""
    url = validate_api_url(str(client.base_url))
    try:
        response = client.get('/auth/me')
    except httpx.RequestError:
        raise RuntimeError('Cannot contact the authentication API. Check the server address.') from None
    if response.status_code == 200:
        try:
            session = response.json()
        except ValueError:
            raise RuntimeError('Authentication API returned an invalid response.') from None
        if not isinstance(session, dict):
            raise RuntimeError('Authentication API returned an invalid response.')
        if session.get('auth_enabled') is False:
            return False
        if session.get('user') and isinstance(session.get('csrf_token'), str) and session['csrf_token']:
            client.headers['X-CSRF-Token'] = session['csrf_token']
            return True
    elif response.status_code != 401:
        raise RuntimeError('Cannot check the session. Verify that the API is available.')
    if not email:
        raise RuntimeError('This API requires login. Register in Tirek, then run with --email your@email.')
    if not safe_for_credentials(url):
        raise RuntimeError('Login requires HTTPS or a loopback API (127.0.0.1, ::1 or localhost).')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            password = (password_prompt or getpass.getpass)('Tirek password (hidden): ')
    except (EOFError, KeyboardInterrupt, getpass.GetPassWarning):
        raise RuntimeError('A private password prompt is unavailable or was cancelled. Run in an interactive terminal.') from None
    try:
        response = client.post('/auth/login', json={'email': email, 'password': password}, follow_redirects=False)
    except httpx.RequestError:
        raise RuntimeError('Login request failed. Check the connection and try again.') from None
    finally:
        # No password is retained in cookies, headers, command arguments or logs.
        password = None
    if response.status_code == 401:
        raise RuntimeError('Incorrect email or password.')
    if response.status_code == 429:
        raise RuntimeError('Too many login attempts. Wait before trying again.')
    if response.status_code != 200:
        raise RuntimeError('Login failed. Check the authentication service; redirects are not followed.')
    try:
        session = response.json()
    except ValueError:
        raise RuntimeError('Login returned an invalid session response.') from None
    if (not isinstance(session, dict) or not session.get('user')
            or not isinstance(session.get('csrf_token'), str) or not session['csrf_token']):
        raise RuntimeError('Login returned an incomplete session response.')
    client.headers['X-CSRF-Token'] = session['csrf_token']
    return True


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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--supplier', required=True, choices=['iek', 'systeme-electric'])
    parser.add_argument('--api', default='http://127.0.0.1:8000/api/v1')
    parser.add_argument('--email', help='Tirek account email; password is requested privately, never as an argument.')
    parser.add_argument('--forecast', action='store_true')
    args = parser.parse_args(argv)
    try:
        validate_api_url(args.api)
    except RuntimeError as error:
        parser.error(str(error))
    files = sorted(args.directory.glob('*.xlsx'))
    if not 1 <= len(files) <= 6:
        parser.error('The directory must contain 1-6 XLSX files.')
    with httpx.Client(base_url=args.api, timeout=60) as client, ExitStack() as stack:
        try:
            authenticate(client, args.email)
        except RuntimeError as error:
            parser.error(str(error))
        uploaded = client.post('/datasets/import', headers={'Idempotency-Key': str(uuid4())},
                               data={'supplier_id': args.supplier}, files=[
            ('files', (path.name, stack.enter_context(path.open('rb')),
                       'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')) for path in files])
        uploaded.raise_for_status()
        dataset_id = wait(client, uploaded.json())
        dataset_response = client.get('/datasets/' + dataset_id)
        dataset_response.raise_for_status()
        dataset = dataset_response.json()
        print(f"Dataset: {dataset_id}; SKUs: {dataset['sku_count']}; data_as_of: {dataset['data_as_of']}", flush=True)
        if not args.forecast:
            return
        if not dataset['calculation_allowed']:
            raise RuntimeError('Dataset requires additional sources before forecasting.')
        response = client.post('/calculations', headers={'Idempotency-Key': str(uuid4())}, json={
            'dataset_id': dataset_id, 'as_of_date': dataset['data_as_of'], 'warehouse_ids': ['almaty'],
            'category_codes': [], 'horizon_days': 28, 'lead_time_days': 7, 'review_period_days': 21,
            'mode': 'operational' if dataset.get('source_kind') == 'observed' else 'scenario',
            'category_policies': [], 'economic_profiles': [],
            'growth_adjustments': [], 'budget_kzt': None, 'request_ai_review': False})
        response.raise_for_status()
        calculation_id = wait(client, response.json())
        result_response = client.get('/calculations/' + calculation_id + '/recommendations')
        result_response.raise_for_status()
        result = result_response.json()
        print(f"Calculation: {calculation_id}; items: {len(result['items'])}", flush=True)
        print('SKU counts by unit:', dict(Counter(item['unit'] for item in result['items'])), flush=True)
        print('Open Tirek with the same account and refresh the workspace.', flush=True)


if __name__ == '__main__':
    main()
