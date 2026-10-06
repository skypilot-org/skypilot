"""Behavioral tests for the QuantaCloud REST client (sky/adaptors/quantacloud.py).

Transport-injected: no network. Every canned response is the bytes the
provider's docs publish, so a regression here is a wire-protocol drift.
"""

import base64
import hashlib
import json
import unittest
from unittest import mock

from sky.adaptors import quantacloud as api


def _page(rows, *, total=None):
    total = len(rows) if total is None else total
    return 200, json.dumps({
        "offers": rows,
        "page": 0,
        "totalPages": 1,
        "totalElements": total,
        "hasMore": False,
    }).encode("utf-8")


def _error(code, message, **extra):
    err = {"code": code, "message": message}
    err.update(extra)
    return json.dumps({"error": err}).encode("utf-8")


class _Transport:
    """Scripted transport: pops one canned response per call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, payload):
        self.calls.append((method, url, dict(headers), payload))
        return self.responses.pop(0)


class TestClientPlumbing(unittest.TestCase):

    def test_api_key_header_on_authenticated_calls_only(self):
        transport = _Transport([
            (200, json.dumps({
                "id": "u1",
                "email": "x@y.z"
            }).encode("utf-8")),
            _page([]),
        ])
        client = api.QuantacloudClient("gpu_live_k", transport=transport)
        client.get_account()
        client.list_offers()
        auth_call, public_call = transport.calls
        self.assertEqual(auth_call[2]["X-API-Key"], "gpu_live_k")
        # The catalog endpoints are PUBLIC: no key on the wire for them.
        self.assertNotIn("X-API-Key", public_call[2])
        self.assertIn("/offers", public_call[1])
        self.assertIn("/account", auth_call[1])

    def test_content_type_json_on_post_bodies(self):
        transport = _Transport([
            (200, json.dumps({
                "id": "k1"
            }).encode("utf-8")),
        ])
        client = api.QuantacloudClient("gpu_live_k", transport=transport)
        client.create_ssh_key("n", "ssh-ed25519 AAAA b@c")
        method, url, headers, payload = transport.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(json.loads(payload), {
            "name": "n",
            "publicKey": "ssh-ed25519 AAAA b@c"
        })

    def test_empty_key_refused(self):
        with self.assertRaises(api.QuantacloudAuthError):
            api.QuantacloudClient("   ")


class TestClientErrors(unittest.TestCase):

    def _client(self, status, body):
        transport = _Transport([(status, body)])
        return api.QuantacloudClient("gpu_live_k",
                                     transport=transport), transport

    def test_401_is_auth_error(self):
        client, _ = self._client(401, b"")
        with self.assertRaises(api.QuantacloudAuthError):
            client.get_account()

    def test_402_carries_the_providers_own_numbers(self):
        body = _error("insufficient_balance",
                      "Insufficient balance. Required: $0.79, available: $0",
                      current_balance="0.000000",
                      required_balance="0.79")
        client, _ = self._client(402, body)
        with self.assertRaises(api.QuantacloudInsufficientBalanceError) as ctx:
            client.create_deployment(offer_id="o1")
        # The stockout-vs-funds distinction lives on the exception: a lane
        # that mistakes this for capacity hunts forever on a billing problem.
        self.assertEqual(ctx.exception.required_balance, "0.79")
        self.assertEqual(ctx.exception.current_balance, "0.000000")
        self.assertEqual(ctx.exception.code, "insufficient_balance")

    def test_404_is_not_found(self):
        client, _ = self._client(404, _error("not_found",
                                             "Deployment not found"))
        with self.assertRaises(api.QuantacloudNotFoundError):
            client.get_deployment("gone")

    def test_429_is_rate_limited_after_bounded_backoff(self):
        transport = _Transport([
            (429, _error("rate_limited", "Too many requests.")),
            (429, _error("rate_limited", "Too many requests.")),
            (429, _error("rate_limited", "Too many requests.")),
            (429, _error("rate_limited", "Too many requests.")),
        ])
        client = api.QuantacloudClient("gpu_live_k", transport=transport)
        with mock.patch.object(api.time, "sleep") as slept:
            with self.assertRaises(api.QuantacloudRateLimited):
                client.get_account()
        self.assertEqual(len(slept.call_args_list),
                         3)  # three retries, then raise

    def test_503_cooling_down_carries_retry_after(self):
        body = _error("offer_cooling_down", "cooling down", retry_after=30)
        client, _ = self._client(503, body)
        with self.assertRaises(api.QuantacloudCoolingDownError) as ctx:
            client.create_deployment(offer_id="o1")
        self.assertEqual(ctx.exception.retry_after_s, 30.0)

    def test_offer_unavailable_is_typed(self):
        client, _ = self._client(
            400, _error("offer_unavailable", "out of stock or withdrawn"))
        with self.assertRaises(api.QuantacloudOfferUnavailableError) as ctx:
            client.create_deployment(offer_id="o1")
        self.assertEqual(ctx.exception.code, "offer_unavailable")

    def test_generic_error_preserves_code_and_body(self):
        client, _ = self._client(500, _error("internal_error", "boom"))
        with self.assertRaises(api.QuantacloudError) as ctx:
            client.get_account()
        self.assertEqual(ctx.exception.code, "internal_error")
        self.assertIn("boom", str(ctx.exception))

    def test_empty_body_error_status_is_error_not_success(self):
        # The empty-body success path must live UNDER the <400 check: a
        # 500 with no body is a FAILURE, never a silent None (the class
        # where a teardown would read "done" while the box keeps billing).
        client, _ = self._client(500, b"")
        with self.assertRaises(api.QuantacloudError):
            client.get_account()

    def test_non_json_body_is_typed_error(self):
        client, _ = self._client(200, b"\xff\xfe not json")
        with self.assertRaises(api.QuantacloudError):
            client.get_account()

    def test_non_dict_list_payload_is_typed_error(self):
        # A list where an object is expected must raise, never AttributeError.
        transport = _Transport([(200, b'["a", "b"]')])
        client = api.QuantacloudClient("gpu_live_k", transport=transport)
        with self.assertRaises(api.QuantacloudError):
            client.get_account()

    def test_no_key_leak_in_errors(self):
        transport = _Transport([(401, _error("", "nope"))])
        client = api.QuantacloudClient("gpu_live_SECRET", transport=transport)
        with self.assertRaises(api.QuantacloudAuthError) as ctx:
            client.get_account()
        self.assertNotIn("SECRET", str(ctx.exception))


class TestPagination(unittest.TestCase):

    def test_short_page_stops(self):
        rows = [{"id": f"o{i}"} for i in range(3)]
        transport = _Transport([_page(rows)])
        client = api.QuantacloudClient("gpu_live_k", transport=transport)
        self.assertEqual(len(client.list_offers()), 3)
        self.assertEqual(len(transport.calls), 1)

    def test_full_page_keeps_paging(self):
        full = [{"id": f"o{i}"} for i in range(api.PAGE_SIZE)]
        short = [{"id": "tail"}]
        transport = _Transport([_page(full), _page(short)])
        client = api.QuantacloudClient("gpu_live_k", transport=transport)
        result = client.list_offers()
        self.assertEqual(len(result), api.PAGE_SIZE + 1)
        self.assertEqual(transport.calls[1][1].split("page=")[1][0], "1")

    def test_runaway_pagination_refuses(self):
        full = [{"id": f"o{i}"} for i in range(api.PAGE_SIZE)]
        transport = _Transport([_page(full)] * api.MAX_PAGES)
        client = api.QuantacloudClient("gpu_live_k", transport=transport)
        with self.assertRaises(api.QuantacloudError) as ctx:
            client.list_offers()
        # A silently truncated catalog is exactly how a provider reads as
        # "no capacity" — the cap must be a loud error.
        self.assertIn("refusing to silently truncate", str(ctx.exception))

    def test_list_rows_key_mismatch_is_typed_error(self):
        transport = _Transport([(200, json.dumps({"data": []}).encode())])
        client = api.QuantacloudClient("gpu_live_k", transport=transport)
        with self.assertRaises(api.QuantacloudError):
            client.list_offers()  # .offers missing: not silently empty


class _Fingerprint:
    """Key fixtures with real material-derived fingerprints."""

    @staticmethod
    def of(public_key: str) -> str:
        blob = base64.b64decode(public_key.strip().split()[1])
        return "SHA256:" + base64.b64encode(
            hashlib.sha256(blob).digest()).decode("ascii")


# Properly-padded base64 blobs (the fingerprint derivation decodes them):
_KEY_BLOB = base64.b64encode(b"quantacloud-test-key-material").decode("ascii")
_KEY_MATERIAL = f"ssh-ed25519 {_KEY_BLOB} test@host"
_OTHER_BLOB = base64.b64encode(b"quantacloud-OTHER-key-material").decode(
    "ascii")
_OTHER_MATERIAL = f"ssh-ed25519 {_OTHER_BLOB} test@host"


class TestEnsureHelpers(unittest.TestCase):

    def _key_row(self, key_id, material, name="anything"):
        return {
            "id": key_id,
            "name": name,
            "managed": False,
            "fingerprint": _Fingerprint.of(material)
        }

    def test_ensure_matches_by_material_not_name(self):
        transport = _Transport([
            (200,
             json.dumps(
                 [self._key_row("k_stale", _OTHER_MATERIAL,
                                name="skypilot")]).encode()),
            (200, json.dumps({
                "id": "k_new"
            }).encode()),
        ])
        client = api.QuantacloudClient("gpu_live_k", transport=transport)
        key_id = client.ensure_ssh_key("skypilot", _KEY_MATERIAL)
        # The stale key shares the NAME; the material differs: a create
        # must have been issued (2 calls: list + create, both consumed).
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(key_id, "k_new")

    def test_ensure_returns_existing_material_match(self):
        transport = _Transport([
            (200,
             json.dumps([
                 self._key_row("k_ours",
                               _KEY_MATERIAL,
                               name="renamed-by-a-human")
             ]).encode()),
        ])
        client = api.QuantacloudClient("gpu_live_k", transport=transport)
        key_id = client.ensure_ssh_key("skypilot", _KEY_MATERIAL)
        self.assertEqual(key_id, "k_ours")
        self.assertEqual(len(transport.calls), 1)  # no duplicate minted

    def test_ensure_rejects_malformed_public_key(self):
        client = api.QuantacloudClient("gpu_live_k", transport=_Transport([]))
        with self.assertRaises(api.QuantacloudError):
            client.ensure_ssh_key("skypilot", "not-a-key")


class _FakeClock:

    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _deployment(status, failure_code=None, ssh_user=None):
    deployment = {"id": "d1", "status": status}
    if failure_code:
        deployment["failure_code"] = failure_code
    if ssh_user:
        deployment["connection"] = {
            "public_ip": "198.51.100.7",
            "ssh_port": 22,
            "ssh_user": ssh_user
        }
    return deployment


class TestWaitUntilActive(unittest.TestCase):

    def _client(self, statuses, deployments=None):
        canned = [json.dumps(_deployment(s)).encode("utf-8") for s in statuses]
        transport = _Transport([(200, body) for body in canned])
        return api.QuantacloudClient("gpu_live_k", transport=transport)

    def test_active_returns(self):
        client = self._client(["provisioning", "connecting", "active"])
        clock = _FakeClock()
        deployment = client.wait_until_active("d1",
                                              poll_interval_s=10,
                                              sleep=clock.sleep,
                                              now=clock.now)
        self.assertEqual(deployment["status"], "active")

    def test_failed_raises_with_the_providers_code(self):
        client = self._client(["provisioning", "failed"])
        clock = _FakeClock()
        with self.assertRaises(api.QuantacloudError) as ctx:
            client.wait_until_active("d1",
                                     poll_interval_s=10,
                                     sleep=clock.sleep,
                                     now=clock.now)
        # never auto-retried — the operator decides.
        self.assertIn("not auto-retrying", str(ctx.exception))

    def test_interrupted_raises(self):
        client = self._client(["provisioning", "interrupted"])
        clock = _FakeClock()
        with self.assertRaises(api.QuantacloudError):
            client.wait_until_active("d1",
                                     poll_interval_s=10,
                                     sleep=clock.sleep,
                                     now=clock.now)

    def test_deadline_fails_closed(self):
        client = self._client(["provisioning"] * 50)
        clock = _FakeClock()
        with self.assertRaises(api.QuantacloudError) as ctx:
            client.wait_until_active("d1",
                                     timeout_s=25,
                                     poll_interval_s=10,
                                     sleep=clock.sleep,
                                     now=clock.now)
        self.assertIn("refusing to wait longer", str(ctx.exception))

    def test_poll_floor_enforced(self):
        client = self._client(["active"])
        with self.assertRaises(api.QuantacloudError):
            client.wait_until_active("d1", poll_interval_s=1)


class TestFindOffers(unittest.TestCase):

    def _offer(self, offer_id, slug, count, region, per_gpu, available=True):
        return {
            "id": offer_id,
            "gpu": {
                "slug": slug,
                "count": count,
                "vramGB": 96
            },
            "gpuCount": count,
            "region": region,
            "pricePerGpu": per_gpu,
            "priceHourly": per_gpu * count,
            "isAvailable": available,
        }

    def test_filters_slug_count_region_and_sorts_cheapest(self):
        offers = [
            self._offer("a", "rtx-pro-6000-blackwell", 1, "us-east-1", 2.59),
            self._offer("b", "rtx-pro-6000-blackwell", 1, "us-east-1", 2.39),
            self._offer("c", "rtx-pro-6000-blackwell", 2, "us-east-1", 2.39),
            self._offer("d", "rtx-6000-ada", 1, "us-east-1", 0.79),
            self._offer("e", "rtx-pro-6000-blackwell", 1, "us-midwest-4", 2.39),
        ]
        transport = _Transport([_page(offers)])
        client = api.QuantacloudClient("gpu_live_k", transport=transport)
        found = client.find_offers(gpu_slug="rtx-pro-6000-blackwell",
                                   gpu_count=1,
                                   region="us-east-1")
        # UNFILTERED fetch + local filter: the wire request must carry no
        # gpu= parameter (a filtered empty is indistinguishable from
        # no-stock — probed live).
        self.assertNotIn("gpu=", transport.calls[0][1])
        # Both us-east-1 matches return, CHEAPEST FIRST (the provisioner
        # takes offers[0]); wrong-region/wrong-count/wrong-slug drop out.
        self.assertEqual([o["id"] for o in found], ["b", "a"])

    def test_mig_slices_never_satisfy_a_whole_gpu_request(self):
        offers = [
            self._offer("m", "rtx-pro-6000-blackwell-mig-48gb", 1, "us-east-1",
                        1.20),
        ]
        transport = _Transport([_page(offers)])
        client = api.QuantacloudClient("gpu_live_k", transport=transport)
        found = client.find_offers(gpu_slug="rtx-pro-6000-blackwell",
                                   gpu_count=1,
                                   region="us-east-1")
        self.assertEqual(found, [])

    def test_price_cap_filters(self):
        offers = [
            self._offer("a", "rtx-pro-6000-blackwell", 1, "us-east-1", 2.59),
            self._offer("b", "rtx-pro-6000-blackwell", 1, "us-east-1", 2.39),
        ]
        transport = _Transport([_page(offers)])
        client = api.QuantacloudClient("gpu_live_k", transport=transport)
        found = client.find_offers(gpu_slug="rtx-pro-6000-blackwell",
                                   gpu_count=1,
                                   region="us-east-1",
                                   max_price_per_gpu=2.50)
        self.assertEqual([o["id"] for o in found], ["b"])


if __name__ == "__main__":
    unittest.main()
