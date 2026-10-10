"""Unit tests for the optional fastapi-guard middleware wiring."""

import asyncio
import itertools
import os
import unittest

_IP = itertools.count(1)

try:
    import guard  # noqa: F401

    _GUARD_AVAILABLE = True
except ImportError:
    _GUARD_AVAILABLE = False


def _unique_ip():
    n = next(_IP)
    return '198.51.%d.%d' % (n // 250, (n % 250) + 1)


class TestGuardIntegration(unittest.TestCase):
    """Guard wiring tests driven through TestClient (no live server)."""

    def setUp(self):
        self._saved = {
            k: os.environ.pop(k)
            for k in list(os.environ)
            if k.startswith('SKYPILOT_GUARD_')
        }

    def tearDown(self):
        for name in list(os.environ):
            if name.startswith('SKYPILOT_GUARD_'):
                os.environ.pop(name, None)
        os.environ.update(self._saved)

    def _build_app(self):
        import fastapi

        from sky.server.guard_integration import attach_guard

        app = fastapi.FastAPI()

        @app.get('/ping')
        async def ping():
            return {'ok': True}

        attach_guard(app)
        return app

    def _client(self, client_ip):
        from starlette.testclient import TestClient

        class _ClientIPInjector:
            # TestClient hardcodes scope["client"]; the middleware stack must
            # see the test IP, so mutate the scope before it reaches FastAPI.
            def __init__(self, asgi_app):
                self._asgi_app = asgi_app

            async def __call__(self, scope, receive, send):
                if scope['type'] == 'http':
                    scope['client'] = (client_ip, 50000)
                await self._asgi_app(scope, receive, send)

        return TestClient(_ClientIPInjector(self._build_app()))

    def test_disabled_by_default(self):
        import fastapi

        from sky.server.guard_integration import attach_guard

        app = fastapi.FastAPI()
        attach_guard(app)
        self.assertEqual(len(app.user_middleware), 0)

    def test_fail_loud_without_package(self):
        import fastapi

        from sky.server.guard_integration import attach_guard

        if _GUARD_AVAILABLE:
            self.skipTest('fastapi-guard is installed')
        os.environ['SKYPILOT_GUARD_ENABLED'] = 'true'
        with self.assertRaisesRegex(ImportError, r'python >= 3\.10'):
            attach_guard(fastapi.FastAPI())

    def _sync_get(self, client_ip):
        with self._client(client_ip) as client:
            return client.get('/ping').status_code

    @unittest.skipUnless(_GUARD_AVAILABLE, 'fastapi-guard not installed')
    def test_blocked_ip_is_rejected(self):
        blocked = _unique_ip()
        os.environ['SKYPILOT_GUARD_ENABLED'] = 'true'
        os.environ['SKYPILOT_GUARD_BLOCKED_IPS'] = blocked
        self.assertEqual(self._sync_get(blocked), 403)

    @unittest.skipUnless(_GUARD_AVAILABLE, 'fastapi-guard not installed')
    def test_rate_limit_returns_429(self):
        client_ip = _unique_ip()
        os.environ['SKYPILOT_GUARD_ENABLED'] = 'true'
        os.environ['SKYPILOT_GUARD_RATE_LIMIT'] = '2'
        os.environ['SKYPILOT_GUARD_RATE_LIMIT_WINDOW'] = '60'
        with self._client(client_ip) as client:
            codes = [client.get('/ping').status_code for _ in range(3)]
        self.assertEqual(codes, [200, 200, 429])

    @unittest.skipUnless(_GUARD_AVAILABLE, 'fastapi-guard not installed')
    def test_passive_mode_never_blocks(self):
        blocked = _unique_ip()
        os.environ['SKYPILOT_GUARD_ENABLED'] = 'true'
        os.environ['SKYPILOT_GUARD_PASSIVE_MODE'] = 'true'
        os.environ['SKYPILOT_GUARD_BLOCKED_IPS'] = blocked
        self.assertEqual(self._sync_get(blocked), 200)

    @unittest.skipUnless(_GUARD_AVAILABLE, 'fastapi-guard not installed')
    def test_redis_stays_off_without_config(self):
        from sky.server.guard_integration import _build_guard_config

        os.environ['SKYPILOT_GUARD_ENABLED'] = 'true'
        config = _build_guard_config()
        self.assertFalse(config.enable_redis)


if __name__ == '__main__':
    unittest.main()
