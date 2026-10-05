"""Behavioral tests for the Latitude.sh REST client (sky/adaptors/latitude.py).

These exercise actual client calls through the injectable transport — not
source-text inspection — so a regression fails with a behavior mismatch.
The API contract asserted here is the provider's own (JSON:API envelopes,
``errors[0].meta.retry_after`` on 429, open-set ``status``) as published at
https://www.latitude.sh/docs/api-reference/.
"""

import json
import unittest
from unittest import mock

from sky.adaptors import latitude as api


def _page(rows):
    return 200, json.dumps({"data": rows, "meta": {}}).encode("utf-8")


class _Transport:
    """Scripted transport: pops one canned response per call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, payload):
        self.calls.append({
            "method": method,
            "url": url,
            "headers": headers,
            "payload": payload,
        })
        return self.responses.pop(0)


class TestClientPlumbing(unittest.TestCase):
    def test_bearer_and_jsonapi_headers(self):
        transport = _Transport([_page([])])
        client = api.LatitudeClient("sekret", transport=transport)
        client.get_profile()
        headers = transport.calls[0]["headers"]
        self.assertEqual(headers["Authorization"], "Bearer sekret")
        self.assertEqual(headers["Accept"], "application/vnd.api+json")
        # Method + path reach the API; the key never appears in a URL.
        self.assertTrue(transport.calls[0]["url"].startswith(api.API_BASE))
        self.assertNotIn("sekret", transport.calls[0]["url"])

    def test_write_body_is_jsonapi_envelope(self):
        transport = _Transport([(201, json.dumps(
            {"data": {"id": "srv_1", "type": "servers",
                      "attributes": {}}}).encode("utf-8"))])
        client = api.LatitudeClient("k", transport=transport)
        client.create_server(
            project="proj_1", plan="g4-rtx6kpro-large", site="ASH",
            operating_system="ubuntu_24_04_x64_lts", hostname="h",
            ssh_key_ids=["ssh_1"],
        )
        body = json.loads(transport.calls[0]["payload"])
        self.assertEqual(body["data"]["type"], "servers")
        self.assertEqual(
            body["data"]["attributes"]["plan"], "g4-rtx6kpro-large")
        self.assertEqual(body["data"]["attributes"]["billing"], "hourly")

    def test_empty_key_refused(self):
        with self.assertRaises(api.LatitudeAuthError):
            api.LatitudeClient("   ")


class TestClientErrors(unittest.TestCase):
    def _client(self, status, body):
        transport = _Transport([(status,
                                  json.dumps(body).encode("utf-8"))])
        return api.LatitudeClient("k", transport=transport), transport

    def test_401_is_auth_error_without_key(self):
        client, _ = self._client(401, {"errors": [{"code": "UNAUTHORIZED"}]})
        with self.assertRaises(api.LatitudeAuthError) as ctx:
            client.get_profile()
        self.assertNotIn("Bearer", str(ctx.exception))

    def test_404_is_not_found(self):
        client, _ = self._client(404, {"errors": [{"detail": "gone"}]})
        with self.assertRaises(api.LatitudeNotFoundError):
            client.delete_server("srv_x")

    def test_jsonapi_error_detail_surfaces(self):
        # The provider's own refusal body must reach the consumer (the
        # INSUFFICIENT_FUNDS-style blindness that burned the shadeform lane).
        client, _ = self._client(
            409, {"errors": [{"code": "PLAN_UNAVAILABLE",
                              "detail": "no stock at NRT"}]})
        with self.assertRaises(api.LatitudeError) as ctx:
            client.create_server(
                project="p", plan="x", site="NRT",
                operating_system="o", hostname="h", ssh_key_ids=[])
        self.assertIn("PLAN_UNAVAILABLE", str(ctx.exception))
        self.assertIn("no stock at NRT", str(ctx.exception))

    def test_429_honors_retry_after_then_raises(self):
        body = {"errors": [{
            "code": "RATE_LIMIT_EXCEEDED",
            "meta": {"retry_after": 0.5},
        }]}
        transport = _Transport([(429, json.dumps(body).encode("utf-8"))] * 4)
        client = api.LatitudeClient("k", transport=transport)
        with mock.patch("time.sleep") as slept:
            with self.assertRaises(api.LatitudeRateLimited) as ctx:
                client.get_profile()
        # Three bounded backoffs honoring the provider's own hint...
        self.assertEqual(slept.call_count, 3)
        self.assertTrue(all(c.args[0] <= api.MAX_RETRY_AFTER_S
                            for c in slept.call_args_list))
        # ...then a loud failure, not a silent empty answer.
        self.assertEqual(ctx.exception.retry_after_s, 0.5)

    def test_error_with_empty_body_still_raises(self):
        # A 4xx/5xx with an empty body must raise, never read as "done"
        # while the server keeps billing (CodeRabbit on the PR).
        transport = _Transport([(500, b"")])
        client = api.LatitudeClient("k", transport=transport)
        with self.assertRaises(api.LatitudeError):
            client.delete_server("srv_x")

    def test_401_with_empty_body_is_auth_error(self):
        transport = _Transport([(401, b"")])
        client = api.LatitudeClient("k", transport=transport)
        with self.assertRaises(api.LatitudeAuthError):
            client.get_profile()

    def test_204_empty_body_is_success(self):
        transport = _Transport([(204, b"")])
        client = api.LatitudeClient("k", transport=transport)
        self.assertIsNone(client.delete_server("srv_x"))


    def test_non_dict_payload_raises_typed_error(self):
        # A top-level LIST/STRING payload would otherwise die with a bare
        # AttributeError inside .get("data") — an unclassified exception
        # every LatitudeError-only caller misses (CodeRabbit on the PR).
        for payload in ([{"id": "x"}], "just-a-string", 42):
            body = json.dumps(payload).encode("utf-8")
            transport = _Transport([(200, body)])
            client = api.LatitudeClient("k", transport=transport)
            with self.assertRaises(api.LatitudeError) as ctx:
                client.get_profile()
            self.assertIn("expected a JSON:API object", str(ctx.exception))

    def test_non_utf8_body_raises_typed_error(self):
        # UnicodeDecodeError is a ValueError, NOT a JSONDecodeError: without
        # the ValueError catch a binary gateway page would escape raw instead
        # of as LatitudeError (CodeRabbit on the PR). Both success and
        # failure bodies must classify.
        raw = b"\xff\xfe\x00\x00garbage\xff"
        for status in (200, 500):
            transport = _Transport([(status, raw)])
            client = api.LatitudeClient("k", transport=transport)
            with self.assertRaises(api.LatitudeError):
                client.get_profile()

class TestPagination(unittest.TestCase):
    def test_short_page_stops(self):
        # A "full" page is exactly PAGE_SIZE rows; pagination continues
        # only while pages come back full.
        full = [{"id": str(i), "type": "plans", "attributes": {}}
                for i in range(api.PAGE_SIZE)]
        tail = [{"id": "t1"}, {"id": "t2"}, {"id": "t3"}]
        transport = _Transport([_page(full), _page(tail)])
        client = api.LatitudeClient("k", transport=transport)
        plans = client.list_plans()
        self.assertEqual(len(plans), api.PAGE_SIZE + 3)
        self.assertEqual(len(transport.calls), 2)

    def test_runaway_pagination_is_loud(self):
        full = [{"id": str(i)} for i in range(api.PAGE_SIZE)]
        transport = _Transport([_page(full)] * (api.MAX_PAGES + 5))
        client = api.LatitudeClient("k", transport=transport)
        with self.assertRaises(api.LatitudeError) as ctx:
            client.list_plans()
        self.assertIn("refusing to silently truncate", str(ctx.exception))


class TestEnsureHelpers(unittest.TestCase):
    def _key(self, key_id, material):
        return {
            "id": key_id,
            "type": "ssh_keys",
            "attributes": {"public_key": f"ssh-ed25519 {material} me"},
        }

    def test_ssh_key_matched_by_material_not_name(self):
        transport = _Transport([_page([self._key("ssh_old", "SAMEKEY")])])
        client = api.LatitudeClient("k", transport=transport)
        self.assertEqual(
            client.ensure_ssh_key("skypilot", "ssh-ed25519 SAMEKEY me"),
            "ssh_old",
        )
        # Matched on material: exactly one GET, no POST (no duplicate key).
        self.assertEqual([c["method"] for c in transport.calls], ["GET"])

    def test_ssh_key_created_when_material_unknown(self):
        transport = _Transport([
            _page([self._key("ssh_old", "OTHER")]),
            (201, json.dumps(
                {"data": self._key("ssh_new", "NEWKEY")}).encode()),
        ])
        client = api.LatitudeClient("k", transport=transport)
        self.assertEqual(
            client.ensure_ssh_key("skypilot", "ssh-ed25519 NEWKEY me"),
            "ssh_new",
        )
        self.assertEqual(transport.calls[1]["method"], "POST")
        self.assertIn("NEWKEY",
                     json.loads(transport.calls[1]["payload"])
                     ["data"]["attributes"]["public_key"])

    def test_project_adopted_by_slug(self):
        transport = _Transport([
            _page([{"id": "proj_9", "type": "projects",
                    "attributes": {"slug": "skypilot"}}]),
        ])
        client = api.LatitudeClient("k", transport=transport)
        self.assertEqual(client.ensure_project("skypilot"), "proj_9")
        self.assertEqual(len(transport.calls), 1)

    def test_project_created_when_absent(self):
        transport = _Transport([
            _page([]),
            (201, json.dumps({"data": {"id": "proj_2",
                                       "type": "projects",
                                       "attributes": {}}}).encode()),
        ])
        client = api.LatitudeClient("k", transport=transport)
        self.assertEqual(client.ensure_project("skypilot"), "proj_2")


class _FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def _server(status):
    return {
        "id": "srv_1",
        "type": "servers",
        "attributes": {"status": status, "primary_ipv4": "203.0.113.7"},
    }


class TestWaitUntilOn(unittest.TestCase):
    def _client(self, statuses):
        responses = [(200, json.dumps({"data": _server(s)}).encode())
                     for s in statuses]
        transport = _Transport(responses)
        return api.LatitudeClient("k", transport=transport)

    def test_polls_until_on(self):
        client = self._client(["queued", "commissioning", "on"])
        clock = _FakeClock()
        server = client.wait_until_on(
            "srv_1", timeout_s=600, poll_interval_s=15,
            sleep=clock.sleep, now=clock.now)
        self.assertEqual(api.LatitudeClient.server_status(server), "on")

    def test_failed_deployment_fails_closed(self):
        client = self._client(["queued", "failed_deployment"])
        clock = _FakeClock()
        with self.assertRaises(api.LatitudeError) as ctx:
            client.wait_until_on(
                "srv_1", timeout_s=600, poll_interval_s=15,
                sleep=clock.sleep, now=clock.now)
        self.assertIn("failed_deployment", str(ctx.exception))

    def test_deadline_expires_loudly(self):
        client = self._client(["queued"] * 10)
        clock = _FakeClock()
        with self.assertRaises(api.LatitudeError) as ctx:
            client.wait_until_on(
                "srv_1", timeout_s=100, poll_interval_s=15,
                sleep=clock.sleep, now=clock.now)
        self.assertIn("refusing to wait longer", str(ctx.exception))

    def test_poll_floor_enforced(self):
        client = self._client(["on"])
        with self.assertRaises(api.LatitudeError):
            client.wait_until_on("srv_1", poll_interval_s=1)


if __name__ == "__main__":
    unittest.main()
