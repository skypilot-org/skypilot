"""Lane tests for the Prime Intellect carry: the fetcher's row
selection and empty-200 cross-check, the provisioner's lifecycle, and
the cloud wiring points that were real lane-killing bugs on previous
carries. Transport/stub-injected: no network."""

import json
import os
import tempfile
import unittest
from unittest import mock

from sky.catalog.data_fetchers import fetch_primeintellect as fetcher


def _offer(provider="dc_gnu", gpu_type="RTX_PRO_6000B_96GB", count=1,
           price=1.35, data_center="us-east-1", country="US",
           gpu_memory=96, stock="Available", vcpu=16, memory=144, disk=725,
           images=None, cloud_id="gpu_1x"):
    return {
        "cloudId": cloud_id,
        "gpuType": gpu_type,
        "socket": "PCIe",
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
        "security": "secure_cloud",
        "prices": {"onDemand": price, "isVariable": False,
                   "currency": "USD"},
        "images": images if images is not None else ["ubuntu_22_cuda_12"],
    }


def _summary_entry(price=1.35):
    return {"cheapest": {"onDemand": price, "communityPrice": None,
                         "spotPrice": None},
            "united_states": {"onDemand": price, "communityPrice": None,
                              "spotPrice": None}}


class TestFetcherRows(unittest.TestCase):

    def test_vm_class_offer_emits_token_datacenter_wholebox_price(self):
        rows = list(fetcher.iter_rows([
            _offer(cloud_id="gpu_1x_pro_6000_blackwell_Addendum_2"),
        ]))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row[0],
                         "dc_gnu:RTX_PRO_6000B_96GB:1")  # the stable token
        self.assertEqual(row[1], "RTXPRO6000")  # accelerator family token
        self.assertEqual(row[2], 1)
        self.assertEqual(row[3], 16)
        self.assertEqual(row[4], 144)
        self.assertEqual(row[5], 1.35)  # whole-box price
        self.assertEqual(row[6], "us-east-1")  # the dataCenter token
        self.assertIn("'AcceleratorName': 'RTXPRO6000'", row[7])
        self.assertIn("'SizeInMiB': 98304", row[7])  # 96 GB in MiB
        self.assertEqual(row[8], "")  # no spot tier

    def test_container_class_upstream_dropped(self):
        # runpod is container-shaped: it cannot run the VM bootstrap
        # ladder, so it must never appear as a rentable row (cheaper or
        # not).
        rows = list(fetcher.iter_rows([
            _offer(provider="runpod", price=0.50),
            _offer(provider="dc_gnu", price=1.35),
        ]))
        self.assertEqual([r[0] for r in rows], ["dc_gnu:RTX_PRO_6000B_96GB:1"])

    def test_unknown_stock_and_unpriced_rows_skipped(self):
        rows = list(fetcher.iter_rows([
            _offer(stock="ComingSoon"),
            _offer(price=None),
            _offer(),
        ]))
        self.assertEqual(len(rows), 1)

    def test_same_triple_collapses_to_cheapest(self):
        rows = list(fetcher.iter_rows([
            _offer(price=1.50, cloud_id="c1"),
            _offer(price=1.35, cloud_id="c2"),
            _offer(price=1.90, cloud_id="c3"),
        ]))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][5], 1.35)

    def test_total_gpu_memory_divides_to_per_gpu_vram(self):
        # The 2-GPU Blackwell box reports gpuMemory=192 (TOTAL): the
        # per-GPU VRAM in GpuInfo must be 96, or a claim believes it
        # rented 192GB per card.
        rows = list(fetcher.iter_rows([
            _offer(count=2, price=2.70, gpu_memory=192),
        ]))
        self.assertIn("'SizeInMiB': 98304", rows[0][7])
        self.assertEqual(rows[0][5], 2.70)

    def test_accelerator_map_per_family(self):
        # The Ada/A6000 families map to their own tokens, but ONLY from
        # VM-class upstreams — the probe's massedcompute rows are
        # third-party (unmeasured deployment model) and drop.
        rows = list(fetcher.iter_rows([
            _offer(gpu_type="RTX6000Ada_48GB", provider="dc_kudu",
                   data_center="us-east-1", price=0.75, gpu_memory=48,
                   vcpu=12, memory=72, disk=350),
            _offer(gpu_type="A6000_48GB", provider="dc_kudu",
                   data_center="us-east-1", price=0.54, gpu_memory=48,
                   vcpu=6, memory=48, disk=256),
            _offer(gpu_type="RTX6000Ada_48GB", provider="massedcompute",
                   data_center="us-central-2", price=0.75, gpu_memory=48,
                   vcpu=12, memory=72, disk=350),
        ]))
        self.assertEqual([r[1] for r in rows], ["RTX6000ADA", "RTXA6000"])

    def test_token_round_trip(self):
        token = fetcher.instance_type_token("dc_gnu",
                                            "RTX_PRO_6000B_96GB", 1)
        self.assertEqual(token, "dc_gnu:RTX_PRO_6000B_96GB:1")
        self.assertEqual(
            fetcher.parse_instance_type_token(token),
            ("dc_gnu", "RTX_PRO_6000B_96GB", 1))

    def test_parse_rejects_garbage(self):
        with self.assertRaises(fetcher.PrimeIntellectCatalogError):
            fetcher.parse_instance_type_token("not-a-token")
        with self.assertRaises(fetcher.PrimeIntellectCatalogError):
            fetcher.parse_instance_type_token("dc_gnu:GPU:x")


class TestZeroStockCrossCheck(unittest.TestCase):
    """The empty-200 ruling: a 200-empty is NOT zero-stock until the
    gpu-summary second opinion agrees (orchestrator binding 2026-10-06)."""

    def test_both_empty_agrees_header_only(self):
        # Endpoints agree on empty: honest zero-stock, header-only write.
        fetcher.verify_zero_stock([], {})

    def test_summary_priced_while_offers_empty_refuses(self):
        # The flaky replica: offers says empty, the independent endpoint
        # prices families — REFUSE, never write a blank catalog.
        with self.assertRaises(fetcher.PrimeIntellectCatalogError):
            fetcher.verify_zero_stock(
                [], {"RTX_PRO_6000B_96GB": {"1": _summary_entry()}})

    def test_missing_second_opinion_refuses(self):
        # An unverified empty is a fetch failure, never a sellout.
        with self.assertRaises(fetcher.PrimeIntellectCatalogError):
            fetcher.verify_zero_stock([], None)

    def test_nonempty_offers_skip_verification(self):
        fetcher.verify_zero_stock([_offer()], None)  # no raise

    def test_main_writes_header_only_csv_on_agreeing_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "vms.csv")
            fixture = os.path.join(tmp, "fixture.json")
            with open(fixture, "w", encoding="utf-8") as handle:
                json.dump({"offers": [], "summary": {}}, handle)
            # argparse's exit path is irrelevant; call main with args.
            self.assertEqual(fetcher.main(["--from-fixture", fixture,
                                           "--output", out]), 0)
            with open(out, encoding="utf-8") as handle:
                content = handle.read()
            self.assertEqual(
                content.strip(),
                ",".join(fetcher.CSV_COLUMNS))  # header-only: honest zero


# -- provisioner (stubbed client: no network, no state file — pods are
# found BY NAME, which is the API's own identity) ------------------------

class _StubClient:
    """Answers exactly what the instance module asks, recording calls."""

    def __init__(self, pods=None, offers=None, fail_create=None):
        self.pods = list(pods or [])
        self.offers = list(offers or [])
        self.calls = []
        self.fail_create = fail_create

    def list_pods(self, live_only=False):
        self.calls.append(("list_pods", live_only))
        rows = self.pods
        if live_only:
            rows = [p for p in rows if p.get("status") not in
                    ("TERMINATED", "DELETING")]
        return rows

    def get_pod(self, pod_id):
        self.calls.append(("get_pod", pod_id))
        for pod in self.pods:
            if pod["id"] == pod_id:
                return pod
        raise RuntimeError("no pod")  # tests never hit the 404 path

    def ensure_ssh_key(self, name, public_key):
        self.calls.append(("ensure_ssh_key", name))
        return "key-id"

    def find_offers(self, **kwargs):
        self.calls.append(("find_offers", kwargs))
        return self.offers

    def create_pod(self, name, offer):
        self.calls.append(("create_pod", name, offer["cloudId"]))
        if self.fail_create:
            raise self.fail_create
        pod = {"id": "pod-new", "name": name, "status": "ACTIVE",
               "sshConnection": "root@10.0.0.1 -p 22", "ip": "10.0.0.1",
               "providerType": offer.get("provider")}
        self.pods.append(pod)
        return pod

    def wait_until_active(self, pod_id):
        self.calls.append(("wait_until_active", pod_id))
        return self.get_pod(pod_id)

    def delete_pod(self, pod_id):
        self.calls.append(("delete_pod", pod_id))
        self.pods = [p for p in self.pods if p["id"] != pod_id]


def _config(node_config=None, count=1):
    from sky.provision import common
    config = mock.Mock(spec=common.ProvisionConfig)
    config.count = count
    config.node_config = node_config if node_config is not None else {
        "InstanceType": "dc_gnu:RTX_PRO_6000B_96GB:1",
        "PublicKey": "ssh-rsa AAAA real-key",
    }
    return config


def _pod(pod_id, status, name="sky-test-head", user="root"):
    return {"id": pod_id, "name": name, "status": status,
            "sshConnection": f"{user}@10.0.0.1 -p 22",
            "ip": "10.0.0.1", "providerType": "dc_gnu"}


class TestProvisioner(unittest.TestCase):

    def _patch_client(self, stub):
        from sky.provision.primeintellect import instance
        return mock.patch.object(instance, "_client", return_value=stub)

    def test_create_reresolves_the_live_offer_and_names_the_pod(self):
        from sky.provision.primeintellect import instance

        stub = _StubClient(
            offers=[_offer()],
        )
        with self._patch_client(stub):
            record = instance.run_instances(
                "us-east-1", "sky-test", "sky-test", _config())
        # The launch re-resolved against the LIVE list with the
        # dataCenter token, and the pod carries the cluster's name.
        find_call = [c for c in stub.calls if c[0] == "find_offers"]
        self.assertEqual(find_call[0][1]["gpu_type"], "RTX_PRO_6000B_96GB")
        self.assertEqual(find_call[0][1]["data_center"], "us-east-1")
        create_call = [c for c in stub.calls if c[0] == "create_pod"]
        self.assertEqual(create_call[0][1], "sky-test-head")
        self.assertEqual(record.head_instance_id, "pod-new")
        self.assertEqual(record.provider_name, "primeintellect")
        self.assertEqual(record.created_instance_ids, ["pod-new"])

    def test_unsubstituted_public_key_placeholder_refused(self):
        from sky.provision.primeintellect import instance

        stub = _StubClient(offers=[_offer()])
        with self._patch_client(stub):
            with self.assertRaises(instance.utils.PrimeintellectError):
                instance.run_instances(
                    "us-east-1", "sky-test", "sky-test",
                    _config(node_config={
                        "InstanceType": "dc_gnu:RTX_PRO_6000B_96GB:1",
                        "PublicKey": "skypilot:ssh_public_key_content",
                    }))
        self.assertFalse([c for c in stub.calls
                          if c[0] == "create_pod"])

    def test_no_live_offer_fails_closed(self):
        from sky.provision.primeintellect import instance
        from sky import exceptions

        stub = _StubClient(offers=[])
        with self._patch_client(stub):
            with self.assertRaises(exceptions.ResourcesUnavailableError):
                instance.run_instances(
                    "us-east-1", "sky-test", "sky-test", _config())

    def test_adopts_named_live_pod(self):
        from sky.provision.primeintellect import instance

        stub = _StubClient(pods=[_pod("p-live", "ACTIVE")])
        with self._patch_client(stub):
            record = instance.run_instances(
                "us-east-1", "sky-test", "sky-test", _config())
        self.assertEqual(record.head_instance_id, "p-live")
        self.assertEqual(record.created_instance_ids, [])
        self.assertFalse([c for c in stub.calls if c[0] == "create_pod"])

    def test_error_pod_adoption_refused(self):
        # ERROR is a steady state that can never reach ready — fail
        # fast, never wait on it (the adoption rule).
        from sky.provision.primeintellect import instance

        stub = _StubClient(pods=[_pod("p-err", "ERROR",
                                     name="sky-test-head")])
        with self._patch_client(stub):
            with self.assertRaises(instance.utils.PrimeintellectError):
                instance.run_instances(
                    "us-east-1", "sky-test", "sky-test", _config())

    def test_terminated_named_pod_is_not_adopted(self):
        # A TERMINATED pod with our name is history, not an endpoint —
        # the lane relaunches (into a foreign-pod refusal if others are
        # live, as designed).
        from sky.provision.primeintellect import instance
        from sky import exceptions

        stub = _StubClient(pods=[_pod("p-old", "TERMINATED",
                                      name="sky-test-head")])
        with self._patch_client(stub):
            # No live pods anywhere and no live offers -> capacity refusal
            # (NOT an adoption of the dead pod).
            with self.assertRaises(exceptions.ResourcesUnavailableError):
                instance.run_instances(
                    "us-east-1", "sky-test", "sky-test", _config())

    def test_unmapped_foreign_live_pod_refuses_to_guess(self):
        from sky.provision.primeintellect import instance

        stub = _StubClient(pods=[_pod("p-x", "ACTIVE", name="console-box")])
        with self._patch_client(stub):
            with self.assertRaises(instance.utils.PrimeintellectError) as ctx:
                instance.run_instances(
                    "us-east-1", "sky-test", "sky-test", _config())
        self.assertIn("refusing to guess", str(ctx.exception))

    def test_terminate_deletes_named_pods_only(self):
        from sky.provision.primeintellect import instance

        stub = _StubClient(pods=[
            _pod("p-mine", "ACTIVE", name="sky-test-head"),
            _pod("p-theirs", "ACTIVE", name="console-box"),
        ])
        with self._patch_client(stub):
            instance.terminate_instances(
                "us-east-1", "sky-test", "sky-test", None)
        self.assertEqual([c for c in stub.calls if c[0] == "delete_pod"],
                         [("delete_pod", "p-mine")])  # ours only, never
        # the console experiment

    def test_terminate_refuses_an_empty_cluster_name(self):
        from sky.provision.primeintellect import instance

        stub = _StubClient()
        with self._patch_client(stub):
            with self.assertRaises(instance.utils.PrimeintellectError):
                instance.terminate_instances("us-east-1", "sky-test", "",
                                             None)
        self.assertFalse(stub.calls)

    def test_stop_is_refused_loudly(self):
        # There is no stop endpoint; a "stop" that silently destroys the
        # box (and its disk) is the worst possible interpretation.
        from sky.provision.primeintellect import instance

        with self.assertRaises(NotImplementedError):
            instance.stop_instances("us-east-1", "sky-test", "sky-test",
                                    None)

    def test_query_status_maps_steady_states(self):
        from sky.provision import common
        from sky.provision.primeintellect import instance
        from sky.utils import status_lib

        stub = _StubClient(pods=[
            _pod("p-a", "ACTIVE", name="sky-test-head"),
            _pod("p-b", "PROVISIONING", name="sky-test-head"),
        ])
        with self._patch_client(stub):
            statuses = instance.query_instances("sky-test", "sky-test")
        self.assertEqual(statuses["p-a"][0], status_lib.ClusterStatus.UP)
        # Transitional rows are excluded under non_terminated_only.
        self.assertNotIn("p-b", statuses)


class TestCloudWiring(unittest.TestCase):
    """The registration points an out-of-tree/in-tree cloud must not
    miss — each of these was a real lane-killing bug on a previous carry
    (Shadeform's _REPR, Spheron's auth branch, Vast's template map)."""

    def test_repr_is_the_catalog_name(self):
        from sky.clouds import PrimeIntellect
        self.assertEqual(PrimeIntellect._REPR, "PrimeIntellect")

    def test_all_clouds_contains_primeintellect(self):
        from sky.skylet import constants
        self.assertIn("primeintellect", constants.ALL_CLOUDS)

    def test_cluster_config_template_registered(self):
        from sky.backends import cloud_vm_ray_backend
        from sky.clouds import PrimeIntellect
        self.assertEqual(
            cloud_vm_ray_backend._get_cluster_config_template(
                PrimeIntellect()),
            "primeintellect-ray.yml.j2",
        )

    def test_auth_dispatch_registers_the_key(self):
        # _add_auth_to_cluster_config routes PrimeIntellect through
        # setup_primeintellect_authentication (the account-level key
        # registration), which ends in configure_ssh_info.
        import ast
        import inspect

        from sky.backends import backend_utils
        source = inspect.getsource(
            backend_utils._add_auth_to_cluster_config)
        tree = ast.parse(source)
        calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and
            isinstance(node.func, ast.Attribute)
        }
        self.assertIn("setup_primeintellect_authentication", calls,
                      "PrimeIntellect missing from the auth dispatch chain")

    def test_zones_provision_loop_yields_per_region(self):
        from sky import clouds as clouds_lib
        from sky.clouds import PrimeIntellect
        with mock.patch.object(
                PrimeIntellect,
                "regions_with_offering",
                classmethod(lambda cls, *a, **k: [
                    clouds_lib.Region("us-east-1"),
                    clouds_lib.Region("us-central-2"),
                ]),
        ):
            yields = list(
                PrimeIntellect.zones_provision_loop(
                    region="us-east-1",
                    num_nodes=1,
                    instance_type="dc_gnu:RTX_PRO_6000B_96GB:1"))
        # One yield PER region with an offering — a single yield for
        # "any" turned a first-region failure into the end of
        # provisioning on the Spheron lane.
        self.assertEqual(yields, [None, None])

    def test_credential_file_mount_is_lane_scoped(self):
        from sky.clouds import PrimeIntellect
        mounts = PrimeIntellect().get_credential_file_mounts()
        self.assertEqual(
            mounts,
            {"~/.prime-intellect/credentials":
             "~/.prime-intellect/credentials"})

    def test_dependency_extra_registered(self):
        # The 'primeintellect' extra must exist so `pip install
        # skypilot[primeintellect]` works and the wheel build stays
        # coherent.
        from sky.setup_files import dependencies
        self.assertIn("primeintellect", dependencies.cloud_dependencies)
        self.assertEqual(
            dependencies.cloud_dependencies["primeintellect"], [])

    def test_provisioner_package_registered(self):
        # The dispatch wrapper resolves `sky.provision.<cloud>` by name;
        # a missing import is a dead lane at first launch.
        from sky.provision import primeintellect
        for op in ("run_instances", "terminate_instances",
                   "query_instances", "get_cluster_info", "stop_instances",
                   "wait_instances", "open_ports", "cleanup_ports"):
            self.assertTrue(hasattr(primeintellect, op),
                            f"provisioner missing {op}")

    def test_stop_and_spot_unsupported_mirror_the_api(self):
        # There is no stop endpoint (DELETE-only teardown) and the lane
        # rents on-demand only: the feature map must say so, and the
        # controller's _SPOT_UNSUPPORTED_CLOUDS invariant test reads this
        # same map.
        from sky import clouds
        from sky.clouds import PrimeIntellect
        features = PrimeIntellect._cloud_unsupported_features()
        self.assertIn(clouds.CloudImplementationFeatures.STOP, features)
        self.assertIn(clouds.CloudImplementationFeatures.SPOT_INSTANCE,
                      features)

    def test_check_credentials_accepts_capability_positionally(self):
        # `sky check` calls check_credentials(cloud_capability) with the
        # capability positionally — a no-arg override TypeErrors, the
        # cloud reports DISABLED, and every launch fails with "requires
        # primeintellect which is not enabled" (the urun-sh/skypilot#4
        # bug class).
        import inspect
        from sky.clouds import PrimeIntellect
        params = list(inspect.signature(
            PrimeIntellect.check_credentials).parameters)
        self.assertEqual(params[0], "cloud_capability")


if __name__ == "__main__":
    unittest.main()
