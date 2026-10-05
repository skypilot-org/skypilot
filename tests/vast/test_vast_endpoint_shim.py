"""Behavioral regression: the v1 instances-endpoint shim.

vast.ai deprecated GET /api/v0/instances (the collection) with
`HTTP 410 Gone` on 2026-09-16, and vastai-sdk 0.2.5 swallows the error
with a bare `except: pass` — callers see an empty instance list and the
provisioner polls forever. The shim rewrites ONLY the collection URL to
/api/v1; per-id routes still work on v0 (probed live).

No live API here: we verify the URL rewrite the SDK's call sites will
actually use, the scoping (per-id untouched), idempotency, and the
loud-failure contract on SDK drift.
"""
import unittest

from sky.adaptors import vast


def _args(url='https://console.vast.ai', api_key='test-key'):
    """A minimal argparse.Namespace matching apiurl's contract."""
    import argparse
    return argparse.Namespace(url=url, api_key=api_key, explain=False)


class TestV1InstancesEndpointShim(unittest.TestCase):
    """sky/adaptors/vast.py:_patch_sdk_instances_endpoint."""

    def test_collection_url_is_rewritten_to_v1(self):
        """subpath '/instances' (the list call) must land on /api/v1."""
        vast.vast()  # triggers import + both patches
        from vastai import vast as vast_cli
        url = vast_cli.apiurl(_args(), '/instances', {'owner': 'me'})
        self.assertTrue(
            url.startswith('https://console.vast.ai/api/v1/instances/?'),
            f'collection URL not rewritten to v1: {url}')
        self.assertNotIn('/api/v0/', url)

    def test_per_id_urls_stay_on_v0(self):
        """Per-id routes still work on v0 — must NOT be rewritten."""
        vast.vast()  # triggers import + both patches
        from vastai import vast as vast_cli
        url = vast_cli.apiurl(_args(), '/instances/12345/', {'owner': 'me'})
        self.assertIn('/api/v0/instances/12345/', url,
                      f'per-id URL unexpectedly rewritten: {url}')

    def test_patch_is_idempotent(self):
        """Double patching must not double-wrap apiurl."""
        vast.vast()
        from vastai import vast as vast_cli
        from sky.adaptors.vast import _patch_sdk_instances_endpoint
        first = vast_cli.apiurl
        _patch_sdk_instances_endpoint()
        self.assertIs(vast_cli.apiurl, first,
                      'second patch call must be a no-op (already patched)')

    def test_rewritten_url_shape_matches_live_v1(self):
        """The rewritten URL is exactly the shape vast.ai serves today."""
        vast.vast()
        from vastai import vast as vast_cli
        url = vast_cli.apiurl(_args(), '/instances', {'owner': 'me'})
        # Live 2026-09-16: GET /api/v1/instances/?owner=me&api_key=... → 200
        self.assertEqual(
            url,
            'https://console.vast.ai/api/v1/instances/?owner=me'
            '&api_key=test-key')

    def test_patch_fails_loudly_on_sdk_drift(self):
        """If the SDK loses vast.apiurl, we raise — not silently no-op."""
        from vastai import vast as vast_cli
        saved = vast_cli.apiurl
        from sky.adaptors import vast as _v
        try:
            del vast_cli.apiurl
            _v._vast_sdk = None  # clear singleton
            with self.assertRaises(RuntimeError) as ctx:
                _v.vast()
            self.assertIn('not found', str(ctx.exception))
        finally:
            vast_cli.apiurl = saved
            _v._vast_sdk = None


if __name__ == '__main__':
    unittest.main()
