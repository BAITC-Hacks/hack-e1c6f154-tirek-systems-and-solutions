"""CLI session plumbing, transport restrictions and non-disclosure of credentials."""
import json
import unittest
from unittest.mock import Mock

import httpx

from scripts.import_local import authenticate, validate_api_url


class ImportAuthenticationTests(unittest.TestCase):
    def test_login_cookie_and_csrf_are_reused_for_upload(self):
        secret = 'SYNTHETIC private password'
        calls = []

        def handler(request):
            calls.append(request.url.path)
            if request.url.path.endswith('/auth/me'):
                return httpx.Response(401)
            if request.url.path.endswith('/auth/login'):
                self.assertEqual(json.loads(request.content), {'email': 'buyer@example.test', 'password': secret})
                return httpx.Response(200, json={'user': {'id': 'one'}, 'csrf_token': 'csrf-example'},
                                      headers={'set-cookie': 'tirek_session=session-example; HttpOnly; Path=/'})
            self.assertEqual(request.headers['X-CSRF-Token'], 'csrf-example')
            self.assertIn('tirek_session=session-example', request.headers['cookie'])
            self.assertNotIn(secret, str(request.headers))
            return httpx.Response(202, json={'job_id': 'example'})

        with httpx.Client(base_url='http://127.0.0.1:8000/api/v1', transport=httpx.MockTransport(handler)) as client:
            self.assertTrue(authenticate(client, 'buyer@example.test', lambda _: secret))
            self.assertEqual(client.post('/datasets/import').status_code, 202)
        self.assertEqual(calls, ['/api/v1/auth/me', '/api/v1/auth/login', '/api/v1/datasets/import'])

    def test_explicit_auth_disabled_does_not_prompt_or_login(self):
        prompt = Mock(side_effect=AssertionError('must not prompt'))
        with httpx.Client(base_url='http://127.0.0.1/api/v1', transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={'auth_enabled': False, 'user': None}))) as client:
            self.assertFalse(authenticate(client, 'optional@example.test', prompt))
        prompt.assert_not_called()

    def test_enabled_api_without_email_fails_clearly(self):
        with httpx.Client(base_url='http://localhost/api/v1', transport=httpx.MockTransport(
                lambda _: httpx.Response(401))) as client:
            with self.assertRaisesRegex(RuntimeError, '--email'):
                authenticate(client)

    def test_remote_plaintext_never_prompts_or_posts_credentials(self):
        for host in ('example.test', '192.168.1.8', '127.0.0.1.example.test'):
            calls = []
            prompt = Mock(side_effect=AssertionError('must not prompt'))
            with httpx.Client(base_url=f'http://{host}/api/v1', transport=httpx.MockTransport(
                    lambda request: calls.append(request.method) or httpx.Response(401))) as client:
                with self.assertRaisesRegex(RuntimeError, 'HTTPS or a loopback'):
                    authenticate(client, 'buyer@example.test', prompt)
            self.assertEqual(calls, ['GET'])
            prompt.assert_not_called()

    def test_https_and_ipv6_loopback_allow_login(self):
        for api in ('https://example.test/api/v1', 'http://[::1]:8000/api/v1'):
            def handler(request):
                return (httpx.Response(401) if request.method == 'GET' else
                        httpx.Response(200, json={'user': {'id': 'one'}, 'csrf_token': 'test'}))
            with httpx.Client(base_url=api, transport=httpx.MockTransport(handler)) as client:
                self.assertTrue(authenticate(client, 'buyer@example.test', lambda _: 'test secret'))

    def test_login_failures_do_not_echo_server_body_password_or_email(self):
        for status in (401, 429, 500, 302):
            def handler(request):
                if request.method == 'GET':
                    return httpx.Response(401)
                return httpx.Response(status, text='secret-value buyer@example.test',
                                      headers={'location': 'http://remote.example/login'})
            with httpx.Client(base_url='https://example.test/api/v1', transport=httpx.MockTransport(handler)) as client:
                with self.assertRaises(RuntimeError) as result:
                    authenticate(client, 'buyer@example.test', lambda _: 'secret-value')
            self.assertNotIn('secret-value', str(result.exception))
            self.assertNotIn('buyer@example.test', str(result.exception))

    def test_network_error_does_not_echo_exception_details(self):
        def handler(request):
            if request.method == 'GET':
                return httpx.Response(401)
            raise httpx.ConnectError('secret-value', request=request)
        with httpx.Client(base_url='https://example.test/api/v1', transport=httpx.MockTransport(handler)) as client:
            with self.assertRaisesRegex(RuntimeError, 'Login request failed') as result:
                authenticate(client, 'buyer@example.test', lambda _: 'secret-value')
        self.assertNotIn('secret-value', str(result.exception))

    def test_embedded_credentials_are_rejected_without_echo(self):
        with self.assertRaises(RuntimeError) as result:
            validate_api_url('https://buyer:secret-value@example.test/api/v1')
        self.assertNotIn('secret-value', str(result.exception))


if __name__ == '__main__':
    unittest.main()
