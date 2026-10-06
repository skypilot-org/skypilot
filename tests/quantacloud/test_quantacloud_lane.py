"""Behavioral tests for the QuantaCloud lane outside the client: the
catalog fetcher's row selection, the provisioner's lifecycle decisions
(including the local cluster-identity store — deployments carry no name),
and the cloud's registration wiring.

These exercise actual calls with fixtures/mocks — not source-text
inspection — so a regression fails with a behavior mismatch.
"""

import json
import os
import tempfile
import unittest
from unittest import mock

from sky.catalog.data_fetchers import fetch_quantacloud
from sky.provision import common as provision_common
from sky.provision.quantacloud import instance as provision


def _offer(offer_id="of_1",
           slug="rtx-pro-6000-blackwell",
           count=1,
           region="us-east-1",
           price=2.39,
           per_gpu=None,
           available=True,
           vram=96,
           vcpu=16,
           ram=144):
    per_gpu = (price /
               count) if (per_gpu is None and price is not None) else per_gpu
    return {
        "id": offer_id,
        "gpu": {
            "slug": slug,
            "count": count,
            "vramGB": vram,
            "name": "NVIDIA RTX PRO 6000 Blackwell",
            "architecture": "Blackwell"
        },
        "gpuCount": count,
        "region": region,
        "regionInfo": {
            "countryCode": "US",
            "continent": "North America"
        },
        "priceHourly": price,
        "pricePerGpu": per_gpu,
        "isAvailable": available,
        "specs": {
            "vcpu": vcpu,
            "ram": ram,
            "disk": 725
        },
        "storageMinGB": 725,
        "storageMaxGB": 725,
        "storageCostPerGB": 0.0,
        "nvlinkEnabled": False,
        "deploymentType": "virtual",
        "provider": {
            "name": "QuantaCloud",
            "slug": "quantacloud"
        },
    }


def _family(key="rtx-pro-6000", available=0):
    return {
        "key": key,
        "label": key,
        "cheapestPrice": None,
        "offerCount": 23,
        "availableCount": available,
        "variantSlugs": [f"{key}-blackwell"]
    }


class TestFetcherRows(unittest.TestCase):

    def test_in_stock_offer_emits_token_region_wholebox_price(self):
        rows = list(fetch_quantacloud.iter_rows([_offer()]))
        row, = rows
        self.assertEqual(row[0], "rtx-pro-6000-blackwell:1")  # InstanceType
        self.assertEqual(row[1], "RTXPRO6000")  # AcceleratorName
        self.assertEqual(row[2], 1)
        self.assertEqual(row[5], 2.39)  # whole-box Price
        self.assertEqual(row[6], "us-east-1")  # Region token
        self.assertEqual(row[8], "")  # no spot tier

    def test_mig_slice_never_prices_as_the_whole_gpu(self):
        rows = list(
            fetch_quantacloud.iter_rows([
                _offer(slug="rtx-pro-6000-blackwell-mig-48gb", vram=48),
            ]))
        self.assertEqual(rows, [])

    def test_unpriced_and_unavailable_rows_skipped(self):
        offers = [
            _offer(offer_id="no_price", price=None),
            _offer(offer_id="not_avail", available=False),
        ]
        self.assertEqual(list(fetch_quantacloud.iter_rows(offers)), [])

    def test_same_triple_collapses_to_cheapest(self):
        # Two live offers for (slug, count, region): the catalog row is a
        # rentable quote — the cheapest wins, and the launch re-resolves
        # against the live list anyway.
        offers = [
            _offer(offer_id="dear", price=2.59),
            _offer(offer_id="cheap", price=2.39),
        ]
        rows = list(fetch_quantacloud.iter_rows(offers))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][5], 2.39)

    def test_accelerator_map_per_family(self):
        rows = list(
            fetch_quantacloud.iter_rows([
                _offer(slug="rtx-6000-ada", count=1, price=0.79, vram=48),
                _offer(slug="rtx-a6000", count=1, price=0.48, vram=48),
                _offer(slug="totally-new-gpu", count=1, price=1.0, vram=80),
            ]))
        accs = {r[0].split(":")[0]: r[1] for r in rows}
        self.assertEqual(accs["rtx-6000-ada"], "RTX6000ADA")
        self.assertEqual(accs["rtx-a6000"], "RTXA6000")
        # Unmapped slug passes through compacted (visible for audit, not
        # silently hidden).
        self.assertEqual(accs["totally-new-gpu"], "TOTALLYNEWGPU")

    def test_token_round_trip(self):
        token = fetch_quantacloud.instance_type_token("rtx-pro-6000-blackwell",
                                                      8)
        self.assertEqual(token, "rtx-pro-6000-blackwell:8")
        self.assertEqual(fetch_quantacloud.parse_instance_type_token(token),
                         ("rtx-pro-6000-blackwell", 8))

    def test_parse_rejects_garbage(self):
        for bad in ("", "no-colon", "slug:x", ":-1", ":0", "a:b:c:"):
            with self.assertRaises(fetch_quantacloud.QuantacloudCatalogError):
                fetch_quantacloud.parse_instance_type_token(bad)

    def test_zero_stock_consistent_families_pass(self):
        # Honest sold-out: offers empty AND every family reports zero
        # available. This is the Latitude-style header-only write state.
        fetch_quantacloud.verify_zero_stock(
            [], [_family(available=0),
                 _family(key="a6000", available=0)])

    def test_zero_stock_contradiction_refuses(self):
        # A family reports stock while the offers list is empty: filter bug
        # or API drift — never write "no capacity" from this.
        with self.assertRaises(
                fetch_quantacloud.QuantacloudCatalogError) as ctx:
            fetch_quantacloud.verify_zero_stock(
                [], [_family(available=0),
                     _family(key="a6000", available=16)])
        self.assertIn("refusing", str(ctx.exception))
        self.assertIn("a6000", str(ctx.exception))

    def test_nonempty_offers_skip_verification(self):
        # Stock exists: nothing to cross-check.
        fetch_quantacloud.verify_zero_stock([_offer()], [_family(available=99)])


class _StubClient:
    """Records calls; answers from canned state."""

    def __init__(self, deployments=(), offers=(), ssh_keys=()):
        self.deployments = {str(d["id"]): d for d in deployments}
        self.offers = list(offers)
        self.ssh_keys = list(ssh_keys)
        self.created = []
        self.stopped = []
        self.ensure_key_calls = []
        self.find_calls = []

    # -- catalog -----------------------------------------------------

    def list_offers(self):
        return self.offers

    def find_offers(self,
                    *,
                    gpu_slug,
                    gpu_count,
                    region=None,
                    max_price_per_gpu=None):
        self.find_calls.append((gpu_slug, gpu_count, region))
        out = []
        for o in self.offers:
            gpu = o.get("gpu") or {}
            if (gpu.get("slug") == gpu_slug and
                    gpu.get("count") == gpu_count and
                (region is None or o.get("region") == region) and
                    "-mig-" not in str(gpu.get("slug") or "")):
                out.append(o)
        out.sort(key=lambda o: float(o.get("pricePerGpu") or 0))
        return out

    # -- ssh keys ------------------------------------------------------

    def list_ssh_keys(self):
        return self.ssh_keys

    def create_ssh_key(self, name, public_key):
        self.ensure_key_calls.append((name, public_key))
        return {"id": "key_new"}

    def ensure_ssh_key(self, name, public_key):
        self.ensure_key_calls.append((name, public_key))
        return "key_1"

    # -- deployments ---------------------------------------------------

    def list_deployments(self):
        return list(self.deployments.values())

    def get_deployment(self, deployment_id):
        if str(deployment_id) not in self.deployments:
            raise provision.utils.QuantacloudNotFoundError("gone")
        return self.deployments[str(deployment_id)]

    def create_deployment(self, *, offer_id, ssh_key_ids=None, provider=None):
        self.created.append({
            "offer_id": offer_id,
            "ssh_key_ids": ssh_key_ids,
            "provider": provider
        })
        new = {
            "id": "dep_new",
            "status": "active",
            "connection": {
                "public_ip": "198.51.100.9",
                "ssh_port": 22,
                "ssh_user": "ubuntu"
            }
        }
        self.deployments["dep_new"] = new
        return new

    def stop_deployment(self, deployment_id):
        self.stopped.append(str(deployment_id))

    def wait_until_active(self, deployment_id):
        return self.get_deployment(deployment_id)

    # -- accessors -----------------------------------------------------

    @staticmethod
    def deployment_status(deployment):
        return str(deployment.get("status") or "").strip().lower()

    @staticmethod
    def connection(deployment):
        conn = deployment.get("connection")
        return conn if isinstance(conn, dict) else None

    @staticmethod
    def ssh_user(deployment):
        conn = _StubClient.connection(deployment) or {}
        user = conn.get("ssh_user")
        return str(user) if user else None


def _config(node_config=None):
    return provision_common.ProvisionConfig(
        provider_config={},
        authentication_config={},
        docker_config={},
        node_config=node_config or {},
        count=1,
        tags={},
        resume_stopped_nodes=False,
        ports_to_open_on_launch=None,
    )


def _deployment_row(deployment_id, status, ssh_user=None):
    row = {"id": deployment_id, "status": status}
    if ssh_user:
        row["connection"] = {
            "public_ip": "198.51.100.5",
            "ssh_port": 22,
            "ssh_user": ssh_user
        }
    return row


class _StateIsolated(unittest.TestCase):
    """Runs each test with the provisioner's identity store in a tmp dir."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._state_path = os.path.join(self._tmp.name, "clusters.json")
        patcher = mock.patch.object(provision, "_STATE_PATH", self._state_path)
        patcher.start()
        self.addCleanup(patcher.stop)


class TestProvisioner(_StateIsolated):

    def _run(self, stub, config=None, region="us-east-1"):
        with mock.patch.object(provision.utils,
                               "client_from_env",
                               return_value=stub):
            return provision.run_instances(region, "ignored", "sky-qc-abc",
                                           config or _config())

    def _config_ready(self):
        return _config({
            "InstanceType": "rtx-pro-6000-blackwell:1",
            "PublicKey": "ssh-ed25519 AAAAREALKEY sky",
        })

    def test_create_reresolves_the_live_offer_and_records_identity(self):
        stub = _StubClient(offers=[
            _offer(offer_id="of_dear", price=2.59),
            _offer(offer_id="of_cheap", price=2.39),
        ])
        record = self._run(stub, self._config_ready())
        self.assertEqual(record.provider_name, "quantacloud")
        self.assertEqual(record.head_instance_id, "dep_new")
        self.assertEqual(record.created_instance_ids, ["dep_new"])
        # Launch-time re-resolution: the token was parsed and the CHEAPEST
        # live offer in the region was chosen (UUIDs are ephemeral; a
        # frozen catalog UUID must never reach the deploy).
        self.assertEqual(stub.find_calls,
                         [("rtx-pro-6000-blackwell", 1, "us-east-1")])
        deploy, = stub.created
        self.assertEqual(deploy["offer_id"], "of_cheap")
        self.assertEqual(deploy["ssh_key_ids"], ["key_1"])
        # The identity store carries the mapping for later operations
        # (deployments have no name field to find them by).
        state = json.load(open(provision._STATE_PATH))
        self.assertEqual(state["sky-qc-abc"]["deployment_id"], "dep_new")
        self.assertEqual(state["sky-qc-abc"]["ssh_user"], "ubuntu")

    def test_unsubstituted_public_key_placeholder_refused(self):
        stub = _StubClient(offers=[_offer()])
        config = _config({
            "InstanceType": "rtx-pro-6000-blackwell:1",
            "PublicKey": "skypilot:ssh_public_key_content",
        })
        with self.assertRaises(provision.utils.QuantacloudError) as ctx:
            self._run(stub, config)
        # The refusal names the placeholder so the operator sees the auth
        # hook that never ran (the Spheron carry's exact trap).
        self.assertIn("never substituted", str(ctx.exception))
        self.assertEqual(stub.created, [])

    def test_missing_instance_type_refused(self):
        stub = _StubClient(offers=[_offer()])
        with self.assertRaises(provision.utils.QuantacloudError):
            self._run(stub, _config({"PublicKey": "ssh-ed25519 A sky"}))

    def test_no_live_offer_fails_closed(self):
        # The catalog row's offer went out of stock: refuse rather than
        # rent a different shape (the quote-vs-launch agreement).
        stub = _StubClient(offers=[_offer(region="us-midwest-4")])
        with self.assertRaises(provision.utils.QuantacloudError) as ctx:
            self._run(stub, self._config_ready(), region="us-east-1")
        self.assertIn("no in-stock QuantaCloud offer", str(ctx.exception))
        self.assertEqual(stub.created, [])

    def test_adopts_mapped_active_deployment(self):
        stub = _StubClient(deployments=[
            _deployment_row("dep_9", "active", ssh_user="ubuntu"),
        ])
        with open(provision._STATE_PATH, "w") as handle:
            json.dump({"sky-qc-abc": {"deployment_id": "dep_9"}}, handle)
        record = self._run(stub, _config({"InstanceType": "x"}))
        self.assertEqual(record.head_instance_id, "dep_9")
        self.assertEqual(record.created_instance_ids, [])
        self.assertEqual(stub.created, [])

    def test_mapped_transitional_deployment_waits(self):
        stub = _StubClient(deployments=[
            _deployment_row("dep_9", "provisioning"),
        ])
        with open(provision._STATE_PATH, "w") as handle:
            json.dump({"sky-qc-abc": {"deployment_id": "dep_9"}}, handle)
        record = self._run(stub, _config({"InstanceType": "x"}))
        self.assertEqual(record.head_instance_id, "dep_9")

    def test_failed_deployment_adoption_refused(self):
        stub = _StubClient(deployments=[
            _deployment_row("dep_9", "failed"),
        ])
        with open(provision._STATE_PATH, "w") as handle:
            json.dump({"sky-qc-abc": {"deployment_id": "dep_9"}}, handle)
        with self.assertRaises(provision.utils.QuantacloudError) as ctx:
            self._run(stub, _config({"InstanceType": "x"}))
        self.assertIn("failed", str(ctx.exception))
        self.assertEqual(stub.created, [])

    def test_stale_mapping_forgotten_then_created(self):
        # The mapped deployment is terminated history server-side: the
        # mapping is dropped and a fresh deployment is created.
        stub = _StubClient(
            deployments=[_deployment_row("dep_old", "terminated")],
            offers=[_offer()])
        with open(provision._STATE_PATH, "w") as handle:
            json.dump({"sky-qc-abc": {"deployment_id": "dep_old"}}, handle)
        record = self._run(stub, self._config_ready())
        self.assertEqual(record.head_instance_id, "dep_new")
        state = json.load(open(provision._STATE_PATH))
        self.assertEqual(state["sky-qc-abc"]["deployment_id"], "dep_new")

    def test_unmapped_live_deployment_refuses_to_guess(self):
        # Deployments have no name: an unmapped live box is someone's (a
        # console experiment or a lost state file) — never a silent adopt.
        stub = _StubClient(deployments=[
            _deployment_row("dep_foreign", "active"),
        ])
        with self.assertRaises(provision.utils.QuantacloudError) as ctx:
            self._run(stub, self._config_ready())
        self.assertIn("refusing to guess", str(ctx.exception))
        self.assertIn("dep_foreign", str(ctx.exception))
        self.assertEqual(stub.created, [])

    def test_terminate_stops_the_mapped_deployment_and_forgets(self):
        stub = _StubClient(deployments=[
            _deployment_row("dep_9", "active"),
        ])
        with open(provision._STATE_PATH, "w") as handle:
            json.dump({"sky-qc-abc": {"deployment_id": "dep_9"}}, handle)
        with mock.patch.object(provision.utils,
                               "client_from_env",
                               return_value=stub):
            provision.terminate_instances("sky-qc-abc")
        self.assertEqual(stub.stopped, ["dep_9"])
        self.assertEqual(json.load(open(provision._STATE_PATH)), {})

    def test_terminate_refuses_an_empty_cluster_name(self):
        stub = _StubClient(deployments=[
            _deployment_row("dep_9", "active"),
        ])
        with mock.patch.object(provision.utils,
                               "client_from_env",
                               return_value=stub):
            with self.assertRaises(provision.utils.QuantacloudError):
                provision.terminate_instances("   ")
        self.assertEqual(stub.stopped, [])

    def test_terminate_unmapped_live_refuses_unmapped_gone_noops(self):
        stub = _StubClient(deployments=[
            _deployment_row("dep_foreign", "active"),
        ])
        with mock.patch.object(provision.utils,
                               "client_from_env",
                               return_value=stub):
            with self.assertRaises(provision.utils.QuantacloudError) as ctx:
                provision.terminate_instances("sky-qc-abc")
        self.assertIn("refusing to guess", str(ctx.exception))
        # With nothing live anywhere, an unmapped terminate is a clean no-op.
        empty = _StubClient()
        with mock.patch.object(provision.utils,
                               "client_from_env",
                               return_value=empty):
            provision.terminate_instances("sky-qc-abc")  # no raise
        self.assertEqual(empty.stopped, [])

    def test_query_maps_status_and_tolerates_unknown_slugs(self):
        stub = _StubClient(deployments=[
            _deployment_row("dep_up", "active"),
            _deployment_row("dep_new_slug", "brand-new-state"),
        ])
        with open(provision._STATE_PATH, "w") as handle:
            json.dump({"sky-qc-abc": {"deployment_id": "dep_up"}}, handle)
        with mock.patch.object(provision.utils,
                               "client_from_env",
                               return_value=stub):
            statuses = provision.query_instances("c", "sky-qc-abc")
        self.assertEqual(statuses["dep_up"][0].value, "UP")

        # An unmapped slug on a DIFFERENT mapped deployment is in-flight,
        # not a crash (the status set is treated as open).
        with open(provision._STATE_PATH, "w") as handle:
            json.dump({"sky-qc-abc": {"deployment_id": "dep_new_slug"}}, handle)
        with mock.patch.object(provision.utils,
                               "client_from_env",
                               return_value=stub):
            statuses = provision.query_instances("c", "sky-qc-abc")
        self.assertIsNone(statuses["dep_new_slug"][0])

    def test_query_404_forgets_and_reports_absence(self):
        stub = _StubClient()  # dep_gone not in the stub: 404
        with open(provision._STATE_PATH, "w") as handle:
            json.dump({"sky-qc-abc": {"deployment_id": "dep_gone"}}, handle)
        with mock.patch.object(provision.utils,
                               "client_from_env",
                               return_value=stub):
            statuses = provision.query_instances("c", "sky-qc-abc")
        # Absence, the same signal a Latitude server list gives when the
        # box was deleted — and the stale mapping is gone for good.
        self.assertEqual(statuses, {})
        self.assertEqual(json.load(open(provision._STATE_PATH)), {})

    def test_query_terminated_history_reports_stopped(self):
        # `terminated` rows persist as account history; the cluster's row
        # must read as not-running (STOPPED — the enum has no TERMINATED;
        # latitude maps failed_deployment the same way).
        stub = _StubClient(deployments=[
            _deployment_row("dep_hist", "terminated"),
        ])
        with open(provision._STATE_PATH, "w") as handle:
            json.dump({"sky-qc-abc": {"deployment_id": "dep_hist"}}, handle)
        with mock.patch.object(provision.utils,
                               "client_from_env",
                               return_value=stub):
            statuses = provision.query_instances("c", "sky-qc-abc")
        self.assertEqual(statuses["dep_hist"][0].value, "STOPPED")

    def test_get_cluster_info_reads_live_ssh_user(self):
        stub = _StubClient(deployments=[
            _deployment_row("dep_9", "active", ssh_user="qcuser"),
        ])
        with open(provision._STATE_PATH, "w") as handle:
            json.dump({"sky-qc-abc": {"deployment_id": "dep_9"}}, handle)
        with mock.patch.object(provision.utils,
                               "client_from_env",
                               return_value=stub):
            info = provision.get_cluster_info("us-east-1", "sky-qc-abc")
        inst, = info.instances["dep_9"]
        self.assertEqual(inst.external_ip, "198.51.100.5")
        self.assertEqual(inst.ssh_port, 22)
        # The provider-reported user wins (the Latitude lesson: docs can
        # lie; the live deployment object is the source of truth).
        self.assertEqual(info.ssh_user, "qcuser")

    def test_stop_instances_is_refused_not_terminate(self):
        """STOP is declared unsupported (stopping terminates and deletes the
        disk): it must raise, never silently DELETE the box — and it must
        BIND to the dispatch contract (CodeRabbit on the Latitude PR: the
        vast-copied `(region, cluster_name, ...)` signature TypeErrors at
        dispatch)."""
        import inspect

        for fn in (provision.stop_instances, provision.terminate_instances):
            sig = inspect.signature(fn)
            self.assertEqual(
                list(sig.parameters),
                ["cluster_name_on_cloud", "provider_config", "worker_only"],
                msg=f"{fn.__name__} must bind the dispatch contract",
            )

        stub = _StubClient(deployments=[
            _deployment_row("dep_9", "active"),
        ])
        with open(provision._STATE_PATH, "w") as handle:
            json.dump({"sky-qc-abc": {"deployment_id": "dep_9"}}, handle)
        with mock.patch.object(provision.utils,
                               "client_from_env",
                               return_value=stub):
            with self.assertRaises(NotImplementedError):
                provision.stop_instances("sky-qc-abc")
        self.assertEqual(stub.stopped, [])

    def test_corrupt_state_file_fails_loud(self):
        with open(provision._STATE_PATH, "w") as handle:
            handle.write("{not json")
        with self.assertRaises(provision.utils.QuantacloudError) as ctx:
            provision._load_state()
        self.assertIn("corrupt", str(ctx.exception))


class TestCloudWiring(unittest.TestCase):
    """The registration points an out-of-tree/in-tree cloud must not miss —
    each of these was a real lane-killing bug on a previous carry
    (Shadeform's _REPR, Spheron's auth branch, Vast's template map)."""

    def test_repr_is_the_catalog_name(self):
        from sky.clouds import Quantacloud
        self.assertEqual(Quantacloud._REPR, "Quantacloud")

    def test_all_clouds_contains_quantacloud(self):
        from sky.skylet import constants
        self.assertIn("quantacloud", constants.ALL_CLOUDS)

    def test_cluster_config_template_registered(self):
        from sky.backends import cloud_vm_ray_backend
        from sky.clouds import Quantacloud
        self.assertEqual(
            cloud_vm_ray_backend._get_cluster_config_template(Quantacloud()),
            "quantacloud-ray.yml.j2",
        )

    def test_auth_dispatch_covers_quantacloud(self):
        # _add_auth_to_cluster_config is an isinstance chain ending in
        # `assert False, cloud`; Quantacloud must take the generic
        # configure_ssh_info branch (its key is registered per-deployment).
        import ast
        import inspect

        from sky.backends import backend_utils
        source = inspect.getsource(backend_utils._add_auth_to_cluster_config)
        tree = ast.parse(source)
        names = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        self.assertIn("Quantacloud", names,
                      "Quantacloud missing from the auth dispatch chain")

    def test_zones_provision_loop_yields_per_region(self):
        from sky import clouds as clouds_lib
        from sky.clouds import Quantacloud
        with mock.patch.object(
                Quantacloud,
                "regions_with_offering",
                classmethod(lambda cls, *a, **k: [
                    clouds_lib.Region("us-east-1"),
                    clouds_lib.Region("us-midwest-1"),
                ]),
        ):
            yields = list(
                Quantacloud.zones_provision_loop(
                    region="us-east-1",
                    num_nodes=1,
                    instance_type="rtx-pro-6000-blackwell:1"))
        # One yield PER region with an offering — a single yield for "any"
        # turned a first-region failure into the end of provisioning on
        # the Spheron lane.
        self.assertEqual(yields, [None, None])

    def test_ssh_key_file_mount_is_quanta_scoped(self):
        from sky.clouds import Quantacloud
        mounts = Quantacloud().get_credential_file_mounts()
        self.assertEqual(mounts,
                         {"~/.quanta/credentials": "~/.quanta/credentials"})

    def test_dependency_extra_registered(self):
        # The 'quantacloud' extra must exist so `pip install
        # skypilot[quantacloud]` works and the wheel build stays coherent.
        from sky.setup_files import dependencies
        self.assertIn("quantacloud", dependencies.cloud_dependencies)
        self.assertEqual(dependencies.cloud_dependencies["quantacloud"], [])

    def test_provisioner_package_registered(self):
        # The dispatch wrapper resolves `sky.provision.<cloud>` by name;
        # a missing import is a dead lane at first launch.
        from sky.provision import quantacloud
        self.assertTrue(hasattr(quantacloud, "run_instances"))


if __name__ == "__main__":
    unittest.main()
