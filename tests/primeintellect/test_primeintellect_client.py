"""Behavioral tests for the Prime Intellect REST client
(sky/adaptors/primeintellect.py).

Transport-injected: no network. Every canned response is the shape the
provider's OpenAPI spec publishes, so a regression here is a
wire-protocol drift. The empty-200 replica flake (observed 2026-10-06)
has first-class tests: a stock decision must never ride on one fetch.
"""

import json
import time
import unittest
from unittest import mock

from sky.adaptors import primeintellect as api


def _page(rows, *, total=None):
    total = len(rows) if total is None else total
    return 200, json.dumps({
        "items": rows,
        "totalCount": total,
    }).encode("utf-8")


def _pods_page(rows, *, total=None):
    total = len(rows) if total is None else total
    return 200, json.dumps({
        "total_count": total,
        "offset": 0,
        "limit": 100,
        "data": rows,
    }).encode("utf-8")


def _detail(message):
    return json.dumps({"detail": message}).encode("utf-8")


def _validation(param, details):
    return 422, json.dumps({
        "errors": [{"param": param, "details": details}]
    }).encode("utf-8")


class _Transport:
    """Scripted transport: pops one canned response per call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, payload):
        self.calls.append((method, url, dict(headers), payload))
        return self.responses.pop(0)


def _offer(cloud_id="gpu_1x", gpu_type="RTX_PRO_6000B_96GB", provider="dc_gnu",
          count=1, price=1.35, data_center="us-east-1", country="US",
          gpu_memory=96, stock="Available", images=None, vcpu=16,
          memory=144, disk=725, socket="PCIe", security="secure_cloud"):
    return {
        "cloudId": cloud_id,
        "gpuType": gpu_type,
        "socket": socket,
        "provider": provider,
        "region": "united_states",
        "dataCenter": data_center,
        "country": country,
        "gpuCount": count,
        "gpuMemory": gpu_memory,
        "vcpu": {"defaultCount": vcpu},
        "memory": {"defaultCount": memory},
        "disk": {"defaultCount": disk},
        "stockStatus": stock,
        "security": security,
        "prices": {"onDemand": price, "isVariable": False,
                   "currency": "USD"},
        "images": images if images is not None else ["ubuntu_22_cuda_12"],
    }


class TestClientPlumbing(unittest.TestCase):

    def test_bearer_header_on_every_call(self):
        transport = _Transport([
            (200, json.dumps({"data": {"id": "u1", "name": "n"}}).encode(
                "utf-8")),
        ])
        client = api.PrimeIntellectClient("pi_key", transport=transport)
        client.get_whoami()
        method, url, headers, _ = transport.calls[0]
        self.assertEqual(headers["Authorization"], "Bearer pi_key")
        self.assertIn("/user/whoami", url)

    def test_content_type_json_on_post_bodies(self):
        transport = _Transport([
            (200, json.dumps({"id": "k1"}).encode("utf-8")),
        ])
        client = api.PrimeIntellectClient("pi_key", transport=transport)
        client.create_ssh_key("n", "ssh-rsa AAAA b@c")
        method, url, headers, payload = transport.calls[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/ssh_keys/"),
                        "collection paths carry a trailing slash")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(json.loads(payload), {
            "name": "n",
            "publicKey": "ssh-rsa AAAA b@c"
        })

    def test_empty_key_refused(self):
        with self.assertRaises(api.PrimeintellectAuthError):
            api.PrimeIntellectClient("   ")


class TestClientErrors(unittest.TestCase):

    def _client(self, status, body):
        transport = _Transport([(status, body)])
        return api.PrimeIntellectClient("pi_key",
                                        transport=transport), transport

    def test_401_is_auth_error(self):
        client, _ = self._client(401, _detail("Invalid token"))
        with self.assertRaises(api.PrimeintellectAuthError):
            client.get_whoami()

    def test_403_is_auth_error_not_capacity(self):
        # The no-token shape is 403 {"detail": "Not authenticated"}; a
        # lane that read it as capacity would hunt forever on an auth bug.
        client, _ = self._client(403, _detail("Not authenticated"))
        with self.assertRaises(api.PrimeintellectAuthError):
            client.list_offers(retry_empty=False)

    def test_404_is_not_found(self):
        client, _ = self._client(404, _detail("Not found"))
        with self.assertRaises(api.PrimeintellectNotFoundError):
            client.get_pod("gone")

    def test_429_is_rate_limited_after_bounded_backoff(self):
        transport = _Transport([
            (429, _detail("Too many requests.")),
            (429, _detail("Too many requests.")),
            (429, _detail("Too many requests.")),
            (429, _detail("Too many requests.")),
        ])
        client = api.PrimeIntellectClient("pi_key", transport=transport)
        with mock.patch.object(time, "sleep") as sleep:
            with self.assertRaises(api.PrimeintellectRateLimited):
                client.get_whoami()
    def test_422_preserves_param_and_details(self):
        offer = _offer()
        client, _ = self._client(*_validation("body.pod.cloudId",
                                              "field required"))
        with self.assertRaises(api.PrimeintellectValidationError) as ctx:
            client.create_pod(name="x", offer=offer)
        self.assertIn("body.pod.cloudId", str(ctx.exception))
        self.assertEqual(ctx.exception.fields,
                         [("body.pod.cloudId", "field required")])

    def test_non_json_body_is_typed_error(self):
        client, _ = self._client(200, b"<html>gateway page</html>")
        with self.assertRaises(api.PrimeintellectError):
            client.get_whoami()

    def test_empty_body_error_status_is_error_not_success(self):
        client, _ = self._client(500, b"")
        with self.assertRaises(api.PrimeintellectError):
            client.get_whoami()

    def test_no_key_leak_in_errors(self):
        client, _ = self._client(401, _detail("Invalid token"))
        try:
            client.get_whoami()
        except api.PrimeintellectAuthError as exc:
            self.assertNotIn("pi_key", str(exc))

    def test_capacity_refusal_is_resources_unavailable(self):
        # A create refused for stock/capacity keeps its body and stays
        # distinguishable from auth (rotate) and validation (fix body).
        body = json.dumps({"detail": "Requested GPU is currently "
                                     "unavailable in this dataCenter"}
                          ).encode("utf-8")
        client, _ = self._client(409, body)
        with self.assertRaises(
                api.PrimeintellectResourcesUnavailableError):
            client.get_whoami()


class TestEmptyFetchFlake(unittest.TestCase):
    """The flaky-replica empty-200 (observed 2026-10-06): a stock
    decision must never ride on one fetch."""

    def test_empty_then_rows_retries_and_returns_rows(self):
        transport = _Transport([
            _page([]),
            _page([_offer()]),
        ])
        client = api.PrimeIntellectClient("pi_key", transport=transport)
        with mock.patch.object(time, "sleep") as sleep:
            rows = client.list_offers()
        self.assertEqual(len(rows), 1)
        # The retry actually happened (the first empty was not trusted).
        self.assertEqual(len(transport.calls), 2)
        sleep.assert_called()

    def test_persistently_empty_returns_empty_after_retries(self):
        transport = _Transport([_page([])] * (api.EMPTY_FETCH_RETRIES + 1))
        client = api.PrimeIntellectClient("pi_key", transport=transport)
        with mock.patch.object(time, "sleep"):
            rows = client.list_offers()
        self.assertEqual(rows, [])
        # All EMPTY_FETCH_RETRIES retries ran before the empty was
        # allowed to mean anything (the fetcher cross-checks next).
        self.assertEqual(len(transport.calls), api.EMPTY_FETCH_RETRIES + 1)

    def test_retry_empty_false_is_off_for_diagnostic_paths(self):
        transport = _Transport([_page([])])
        client = api.PrimeIntellectClient("pi_key", transport=transport)
        self.assertEqual(client.list_offers(retry_empty=False), [])
        self.assertEqual(len(transport.calls), 1)


class TestPagination(unittest.TestCase):

    def test_short_page_stops(self):
        transport = _Transport([_page([_offer()])])
        client = api.PrimeIntellectClient("pi_key", transport=transport)
        self.assertEqual(len(client.list_offers(retry_empty=False)), 1)
        self.assertEqual(len(transport.calls), 1)

    def test_full_page_keeps_paging_until_total(self):
        first = _page([_offer(cloud_id=f"c{i}") for i in range(100)],
                      total=101)
        second = _page([_offer(cloud_id="tail")], total=101)
        transport = _Transport([first, second])
        client = api.PrimeIntellectClient("pi_key", transport=transport)
        rows = client.list_offers(retry_empty=False)
        self.assertEqual(len(rows), 101)
        self.assertEqual(len(transport.calls), 2)

    def test_runaway_pagination_refuses(self):
        full = _page([_offer(cloud_id=f"c{i}") for i in range(100)],
                     total=1000000)
        transport = _Transport([full] * api.MAX_PAGES)
        client = api.PrimeIntellectClient("pi_key", transport=transport)
        with self.assertRaises(api.PrimeintellectError):
            client.list_offers(retry_empty=False)


class TestSshKeys(unittest.TestCase):

    def test_ensure_matches_by_material(self):
        key = {"id": "k1", "name": "other-name",
               "publicKey": "ssh-rsa AAAA b@c"}
        transport = _Transport([
            (200, json.dumps({"total_count": 1, "offset": 0, "limit": 100,
                              "data": [key]}).encode("utf-8")),
        ])
        client = api.PrimeIntellectClient("pi_key", transport=transport)
        # A DIFFERENT name with the SAME material is the same key: no
        # duplicate is minted.
        self.assertEqual(
            client.ensure_ssh_key("skypilot", "ssh-rsa AAAA b@c"), "k1")

    def test_ensure_adds_when_absent(self):
        transport = _Transport([
            (200, json.dumps({"total_count": 0, "offset": 0, "limit": 100,
                              "data": []}).encode("utf-8")),
            (200, json.dumps({"id": "k2"}).encode("utf-8")),
        ])
        client = api.PrimeIntellectClient("pi_key", transport=transport)
        self.assertEqual(
            client.ensure_ssh_key("skypilot", "ssh-rsa AAAA b@c"), "k2")
        method, url, _, payload = transport.calls[1]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/ssh_keys/"))
        self.assertEqual(json.loads(payload), {
            "name": "skypilot",
            "publicKey": "ssh-rsa AAAA b@c",
        })


class TestCreatePod(unittest.TestCase):

    def test_create_echoes_the_offer_row_verbatim(self):
        offer = _offer(cloud_id="gpu_1x_pro_6000_blackwell_Addendum_2",
                       data_center="us-east-1", country="US")
        transport = _Transport([
            (200, json.dumps({"id": "p1", "status": "PROVISIONING"}).encode(
                "utf-8")),
        ])
        client = api.PrimeIntellectClient("pi_key", transport=transport)
        pod = client.create_pod(name="my-head", offer=offer)
        self.assertEqual(pod["id"], "p1")
        method, url, _, payload = transport.calls[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/pods/"))
        body = json.loads(payload)
        # cloudId/gpuType/socket/gpuCount/dataCenterId/country/security all
        # come from the SAME live row — and maxPrice is NOT sent (the
        # docs reserve it for variable-price offers).
        self.assertEqual(body, {
            "pod": {
                "name": "my-head",
                "cloudId": "gpu_1x_pro_6000_blackwell_Addendum_2",
                "gpuType": "RTX_PRO_6000B_96GB",
                "socket": "PCIe",
                "gpuCount": 1,
                "image": "ubuntu_22_cuda_12",
                "security": "secure_cloud",
                "dataCenterId": "us-east-1",
                "country": "US",
            },
            "provider": {"type": "dc_gnu"},
        })

    def test_create_prefers_the_offers_own_image(self):
        offer = _offer(images=["vllm_llama_8b", "ubuntu_26"])
        transport = _Transport([
            (200, json.dumps({"id": "p1"}).encode("utf-8")),
        ])
        client = api.PrimeIntellectClient("pi_key", transport=transport)
        client.create_pod(name="x", offer=offer)
        self.assertEqual(json.loads(transport.calls[0][3])["pod"]["image"],
                         "vllm_llama_8b")

    def test_create_refuses_a_partial_row(self):
        client = api.PrimeIntellectClient(
            "pi_key", transport=_Transport([]))
        with self.assertRaises(api.PrimeintellectValidationError):
            client.create_pod(name="x", offer={"cloudId": "c",
                                               "gpuType": "T"})

    def test_create_response_without_id_is_typed_error(self):
        transport = _Transport([
            (200, json.dumps({"status": "PROVISIONING"}).encode("utf-8")),
        ])
        client = api.PrimeIntellectClient("pi_key", transport=transport)
        with self.assertRaises(api.PrimeintellectError):
            client.create_pod(name="x", offer=_offer())


class TestFindOffers(unittest.TestCase):

    def _client(self, offers):
        transport = _Transport([_page(offers)])
        return api.PrimeIntellectClient("pi_key", transport=transport)

    def test_vm_class_allowlist_drops_container_upstreams(self):
        # runpod is container-shaped: it cannot run the VM bootstrap
        # ladder, so its offers must never satisfy the lane.
        client = self._client([
            _offer(provider="runpod", price=0.50),
            _offer(provider="dc_gnu", price=1.35),
            _offer(provider="primecompute", price=1.40),
        ])
        rows = client.find_offers(gpu_type="RTX_PRO_6000B_96GB",
                                  gpu_count=1)
        self.assertEqual([r["provider"] for r in rows],
                         ["dc_gnu", "primecompute"])

    def test_data_center_and_count_filters(self):
        client = self._client([
            _offer(data_center="us-east-1", count=1),
            _offer(data_center="us-west-1", count=1),
            _offer(data_center="us-east-1", count=2),
        ])
        rows = client.find_offers(gpu_type="RTX_PRO_6000B_96GB",
                                  gpu_count=1, data_center="us-east-1")
        self.assertEqual([r["dataCenter"] for r in rows], ["us-east-1"])

    def test_price_cap_is_per_gpu(self):
        client = self._client([
            _offer(price=1.35),       # 1.35/GPU: in
            _offer(price=2.70, count=2),  # 1.35/GPU: in
            _offer(price=3.00),       # 3.00/GPU: out
        ])
        rows = client.find_offers(gpu_type="RTX_PRO_6000B_96GB",
                                  gpu_count=1, max_price_per_gpu=1.46)
        # Sorted by per-GPU price ascending; the 2-GPU box is excluded by
        # the count filter, the $3.00 one by the cap.
        self.assertEqual([r["prices"]["onDemand"] for r in rows], [1.35])

    def test_unknown_stock_flag_is_not_rentable(self):
        client = self._client([_offer(stock="ReservedOnly")])
        self.assertEqual(
            client.find_offers(gpu_type="RTX_PRO_6000B_96GB", gpu_count=1),
            [])

    def test_sort_is_per_gpu_then_whole_box(self):
        client = self._client([
            _offer(price=2.70, count=2),   # 1.35/GPU
            _offer(price=1.35, count=1),  # 1.35/GPU, cheaper box
        ])
        rows = client.find_offers(gpu_type="RTX_PRO_6000B_96GB",
                                  gpu_count=2)
        self.assertEqual([r["gpuCount"] for r in rows], [2])


class TestWaitUntilActive(unittest.TestCase):

    def _client(self, responses):
        transport = _Transport(responses)
        return api.PrimeIntellectClient("pi_key", transport=transport)

    def test_active_returns_the_pod(self):
        client = self._client([
            (200, json.dumps({"id": "p1", "status": "PROVISIONING"}
                            ).encode("utf-8")),
            (200, json.dumps({"id": "p1", "status": "ACTIVE"}).encode(
                "utf-8")),
        ])
        with mock.patch.object(time, "sleep"):
            pod = client.wait_until_active("p1", poll_interval_s=10.0)
        self.assertEqual(pod["status"], "ACTIVE")

    def test_error_fails_closed_with_the_providers_reason(self):
        client = self._client([
            (200, json.dumps({"id": "p1", "status": "ERROR",
                              "installationFailure":
                              "image pull failed"}).encode("utf-8")),
        ])
        with self.assertRaises(api.PrimeintellectError) as ctx:
            client.wait_until_active("p1", poll_interval_s=10.0)
        self.assertIn("image pull failed", str(ctx.exception))

    def test_terminated_midwait_raises_not_loops(self):
        # The wallet-auto-delete path: the box died while we waited.
        client = self._client([
            (200, json.dumps({"id": "p1", "status": "TERMINATED"}
                            ).encode("utf-8")),
        ])
        with self.assertRaises(api.PrimeintellectError) as ctx:
            client.wait_until_active("p1", poll_interval_s=10.0)
        self.assertIn("deleted mid-provision", str(ctx.exception))

    def test_deadline_fails_closed(self):
        pod = json.dumps({"id": "p1", "status": "PROVISIONING"}).encode(
            "utf-8")
        client = self._client([(200, pod)] * 100)

        clock = {"now": 0.0}

        def fake_now():
            clock["now"] += 30.0
            return clock["now"]

        with mock.patch.object(time, "sleep"):
            with self.assertRaises(api.PrimeintellectError) as ctx:
                client.wait_until_active("p1", timeout_s=120,
                                         poll_interval_s=10.0,
                                         now=fake_now)
        self.assertIn("refusing to wait longer", str(ctx.exception))

    def test_poll_floor_is_enforced(self):
        client = self._client([])
        with self.assertRaises(api.PrimeintellectError):
            client.wait_until_active("p1", poll_interval_s=0.5)


class TestTeardownAndAccessors(unittest.TestCase):

    def test_delete_404_is_success(self):
        transport = _Transport([(404, _detail("Not found"))])
        client = api.PrimeIntellectClient("pi_key", transport=transport)
        client.delete_pod("gone")  # no raise: idempotent teardown
        self.assertEqual(transport.calls[0][0], "DELETE")
        self.assertIn("/pods/gone", transport.calls[0][1])

    def test_ssh_user_and_port_from_connection_string(self):
        pod = {"sshConnection": "root@135.23.125.123 -p 22"}
        self.assertEqual(api.PrimeIntellectClient.ssh_user(pod), "root")
        self.assertEqual(api.PrimeIntellectClient.ssh_port(pod), 22)

    def test_port_from_prime_port_mapping(self):
        pod = {"primePortMapping": [
            {"internal": "22", "external": "2222", "protocol": "TCP",
             "usedBy": "SSH"},
        ]}
        self.assertEqual(api.PrimeIntellectClient.ssh_port(pod), 2222)

    def test_list_shaped_ip_and_connection(self):
        # The APIPodConfig schema allows ip/sshConnection as string OR
        # list-of-strings/nulls; both shapes occur.
        pod = {"ip": ["135.23.125.123", None],
               "sshConnection": ["root@135.23.125.123 -p 22", None]}
        self.assertEqual(api.PrimeIntellectClient.pod_ip(pod),
                         "135.23.125.123")
        self.assertEqual(api.PrimeIntellectClient.ssh_user(pod), "root")

    def test_get_pod_status_batches(self):
        transport = _Transport([
            (200, json.dumps({"data": [
                {"podId": "a", "status": "ACTIVE"},
                {"podId": "b", "status": "PROVISIONING"},
            ]}).encode("utf-8")),
        ])
        client = api.PrimeIntellectClient("pi_key", transport=transport)
        rows = client.get_pod_status(["a", "b"])
        self.assertEqual(len(rows), 2)
        url = transport.calls[0][1]
        self.assertIn("pod_ids=a", url)
        self.assertIn("pod_ids=b", url)

    def test_list_pods_filters_dead_rows_for_zero_verification(self):
        transport = _Transport([
            _pods_page([
                {"id": "p1", "name": "x-head", "status": "ACTIVE"},
                {"id": "p2", "name": "y-head", "status": "TERMINATED"},
            ], total=2),
        ])
        client = api.PrimeIntellectClient("pi_key", transport=transport)
        live = client.list_pods(live_only=True)
        self.assertEqual([p["id"] for p in live], ["p1"])


if __name__ == "__main__":
    unittest.main()
