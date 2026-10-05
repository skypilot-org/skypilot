"""Behavioral tests for the Latitude.sh lane outside the client: the
catalog fetcher's row selection, the provisioner's lifecycle decisions,
and the cloud's registration wiring.

These exercise actual calls with fixtures/mocks — not source-text
inspection — so a regression fails with a behavior mismatch.
"""

import json
import os
import tempfile
import unittest
from unittest import mock

from sky.catalog.data_fetchers import fetch_latitude
from sky.provision import common as provision_common
from sky.provision.latitude import instance as provision


def _plan(slug="g4-rtx6kpro-large", gpu="NVIDIA RTX PRO 6000", count=1,
          regions=None, features=("ssh", "user_data")):
    return {
        "id": f"plan_{slug}",
        "type": "plans",
        "attributes": {
            "slug": slug,
            "features": list(features),
            "specs": {
                "cpu": {"type": "EPYC", "clock": 2.4, "cores": 32, "count": 2},
                "memory": {"total": 256},
                "gpu": {"count": count, "type": gpu, "vram_per_gpu": 96},
            },
            "regions": regions if regions is not None else [
                {
                    "name": "North America",
                    "locations": {"available": ["ASH"], "in_stock": ["ASH"]},
                    "stock_level": "high",
                    "pricing": {"USD": {"hour": 3.41, "month": 100}},
                },
            ],
        },
    }


class TestFetcherRows(unittest.TestCase):
    def test_in_stock_priced_site_emits_row_with_site_slug_region(self):
        rows = list(fetch_latitude.iter_rows([_plan()]))
        self.assertEqual(len(rows), 1)
        row = dict(zip(fetch_latitude.CSV_COLUMNS, rows[0]))
        self.assertEqual(row["InstanceType"], "g4-rtx6kpro-large")
        # Region is the SITE slug — the budget's allowedRegions token and
        # the deploy API's `site` value are the same token.
        self.assertEqual(row["Region"], "ASH")
        # Price is the whole box, per-region USD hourly.
        self.assertEqual(row["Price"], 3.41)
        self.assertEqual(row["AcceleratorName"], "RTXPRO6000")
        self.assertEqual(row["AcceleratorCount"], 1)

    def test_out_of_stock_site_is_never_listed(self):
        # available but NOT in_stock: deployable in principle, unrentable
        # now — the catalog shows only what the lane can rent.
        plan = _plan(regions=[{
            "name": "Europe",
            "locations": {"available": ["FRA", "ASH"], "in_stock": []},
            "pricing": {"USD": {"hour": 3.2}},
        }])
        self.assertEqual(list(fetch_latitude.iter_rows([plan])), [])

    def test_unpriced_in_stock_site_is_skipped_not_free(self):
        plan = _plan(regions=[{
            "name": "Somewhere",
            "locations": {"available": ["NRT"], "in_stock": ["NRT"]},
            "pricing": {"USD": {"month": 100}},  # no hourly price
        }])
        self.assertEqual(list(fetch_latitude.iter_rows([plan])), [])

    def test_plan_without_ssh_feature_is_skipped(self):
        plan = _plan(features=("user_data",))
        self.assertEqual(list(fetch_latitude.iter_rows([plan])), [])

    def test_blackwell_family_maps_to_one_accelerator(self):
        for gpu in ("NVIDIA RTX PRO 6000 Max-Q", "NVIDIA RTX PRO 6000 S",
                    "NVIDIA RTX 6000D"):
            rows = list(fetch_latitude.iter_rows([_plan(gpu=gpu)]))
            self.assertEqual(
                dict(zip(fetch_latitude.CSV_COLUMNS, rows[0]))[
                    "AcceleratorName"],
                "RTXPRO6000",
                msg=f"{gpu} must fold into the RTXPRO6000 family",
            )

    def test_multi_gpu_plan_prices_whole_box(self):
        plan = _plan(count=4, regions=[{
            "name": "NA",
            "locations": {"available": ["ASH"], "in_stock": ["ASH"]},
            "pricing": {"USD": {"hour": 10.0}},
        }])
        row = dict(zip(fetch_latitude.CSV_COLUMNS,
                       list(fetch_latitude.iter_rows([plan]))[0]))
        self.assertEqual(row["AcceleratorCount"], 4)
        # The hourly price is for the whole 4-GPU box, not per GPU.
        self.assertEqual(row["Price"], 10.0)

    def test_from_fixture_writes_csv(self):
        plans = [_plan(), _plan(slug="g3-h100-small", gpu="NVIDIA H100",
                                regions=[{
                                    "name": "US",
                                    "locations": {
                                        "available": ["CHI"],
                                        "in_stock": ["CHI"],
                                    },
                                    "pricing": {"USD": {"hour": 7.0}},
                                }])]
        with tempfile.TemporaryDirectory() as tmp:
            fixture = os.path.join(tmp, "fixture.json")
            out = os.path.join(tmp, "latitude", "vms.csv")
            with open(fixture, "w", encoding="utf-8") as handle:
                json.dump({"plans": plans}, handle)
            rc = fetch_latitude.main(
                ["--from-fixture", fixture, "--output", out])
            self.assertEqual(rc, 0)
            with open(out, encoding="utf-8") as handle:
                lines = handle.read().strip().splitlines()
            self.assertEqual(len(lines), 3)  # header + 2 rows
            self.assertIn("g3-h100-small", lines[2])
            # H100 passes through unmapped (real accelerator, visible).
            self.assertIn("H100", lines[2])

    def test_missing_key_refuses_empty_catalog(self):
        env = {fetch_latitude.latitude_api.API_KEY_ENV: ""}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(fetch_latitude.LatitudeCatalogError):
                fetch_latitude.main(["--output", "/tmp/unused.csv"])


class _StubClient:
    """Records calls; answers from canned state."""

    def __init__(self, servers=(), ssh_keys=(), projects=()):
        self.servers = list(servers)
        self.ssh_keys = list(ssh_keys)
        self.projects = list(projects)
        self.created_servers = []
        self.deleted = []
        self.ensure_key_calls = []

    def list_servers(self, hostname=None):
        # JSON:API shape: the hostname lives under attributes.
        return [s for s in self.servers
                if (s.get("attributes") or {}).get("hostname") == hostname]

    def ensure_project(self, slug):
        return f"proj_{slug}"

    def ensure_ssh_key(self, name, public_key):
        self.ensure_key_calls.append((name, public_key))
        return "ssh_1"

    def create_server(self, **kwargs):
        self.created_servers.append(kwargs)
        return {"id": "srv_new", "attributes": {"status": "on"}}

    def wait_until_on(self, server_id):
        return {"id": server_id, "attributes": {"status": "on"}}

    def delete_server(self, server_id):
        self.deleted.append(server_id)

    def list_ssh_keys(self):
        return self.ssh_keys

    def list_projects(self):
        return self.projects


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


def _server_row(server_id, hostname, status):
    return {
        "id": server_id,
        "attributes": {"hostname": hostname, "status": status,
                       "primary_ipv4": "198.51.100.5"},
    }


class TestProvisioner(unittest.TestCase):
    def _run(self, stub, config=None, region="ASH"):
        with mock.patch.object(provision.utils, "client_from_env",
                               return_value=stub):
            return provision.run_instances(
                region, "ignored", "sky-lat-abc", config or _config())

    def test_create_attaches_key_hourly_and_waits(self):
        stub = _StubClient()
        config = _config({
            "InstanceType": "g4-rtx6kpro-large",
            "latitude_operating_system": "ubuntu_24_04_x64_lts",
            "PublicKey": "ssh-ed25519 REALKEY sky",
        })
        record = self._run(stub, config)
        self.assertEqual(record.provider_name, "latitude")
        self.assertEqual(record.head_instance_id, "srv_new")
        self.assertEqual(record.created_instance_ids, ["srv_new"])
        (name, key), = stub.ensure_key_calls
        self.assertEqual(name, "skypilot")
        self.assertEqual(key, "ssh-ed25519 REALKEY sky")
        deploy, = stub.created_servers
        self.assertEqual(deploy["plan"], "g4-rtx6kpro-large")
        self.assertEqual(deploy["site"], "ASH")
        self.assertEqual(deploy["billing"], "hourly")
        self.assertEqual(deploy["ssh_key_ids"], ["ssh_1"])
        self.assertEqual(deploy["operating_system"], "ubuntu_24_04_x64_lts")

    def test_unsubstituted_public_key_placeholder_refused(self):
        stub = _StubClient()
        config = _config({
            "InstanceType": "g4-rtx6kpro-large",
            "PublicKey": "skypilot:ssh_public_key_content",
        })
        with self.assertRaises(provision.utils.LatitudeError) as ctx:
            self._run(stub, config)
        # The refusal names the placeholder so the operator sees the auth
        # hook that never ran (the Spheron carry's exact trap).
        self.assertIn("never substituted", str(ctx.exception))
        self.assertEqual(stub.created_servers, [])

    def test_adopts_existing_on_server(self):
        stub = _StubClient(servers=[
            _server_row("srv_9", "sky-lat-abc", "on")
        ])
        record = self._run(stub, _config({"InstanceType": "x"}))
        self.assertEqual(record.head_instance_id, "srv_9")
        self.assertEqual(record.created_instance_ids, [])
        self.assertEqual(stub.created_servers, [])

    def test_failed_deployment_adoption_refused(self):
        stub = _StubClient(servers=[
            _server_row("srv_9", "sky-lat-abc", "failed_deployment")
        ])
        with self.assertRaises(provision.utils.LatitudeError) as ctx:
            self._run(stub, _config({"InstanceType": "x"}))
        self.assertIn("failed_deployment", str(ctx.exception))
        self.assertEqual(stub.created_servers, [])

    def test_two_live_servers_refused_not_guessed(self):
        stub = _StubClient(servers=[
            _server_row("srv_a", "sky-lat-abc", "on"),
            _server_row("srv_b", "sky-lat-abc", "on"),
        ])
        with self.assertRaises(provision.utils.LatitudeError):
            self._run(stub, _config({"InstanceType": "x"}))

    def test_terminate_deletes_every_live_server(self):
        stub = _StubClient(servers=[
            _server_row("srv_a", "sky-lat-abc", "on"),
        ])
        with mock.patch.object(provision.utils, "client_from_env",
                               return_value=stub):
            provision.terminate_instances("sky-lat-abc")
        self.assertEqual(stub.deleted, ["srv_a"])

    def test_terminate_swallows_only_already_gone(self):
        class _Gone(_StubClient):
            def delete_server(self, server_id):
                raise provision.utils.LatitudeNotFoundError("gone")

        stub = _Gone(servers=[_server_row("srv_a", "sky-lat-abc", "on")])
        with mock.patch.object(provision.utils, "client_from_env",
                               return_value=stub):
            provision.terminate_instances("sky-lat-abc")  # idempotent

    def test_query_instances_maps_and_tolerates_new_slugs(self):
        stub = _StubClient(servers=[
            _server_row("srv_on", "sky-lat-abc", "on"),
            _server_row("srv_new", "sky-lat-abc", "starting_deploy"),
        ])
        with mock.patch.object(provision.utils, "client_from_env",
                               return_value=stub):
            statuses = provision.query_instances("c", "sky-lat-abc")
        # `on` maps UP; an unmapped provisioning slug stays visible as
        # in-flight (the status field is an open set — a new slug must not
        # crash reconcile).
        self.assertEqual(statuses["srv_on"][0].value, "UP")
        self.assertEqual(statuses["srv_new"][0], None)

    def test_get_cluster_info_uses_primary_ipv4_on_port_22(self):
        stub = _StubClient(servers=[
            _server_row("srv_a", "sky-lat-abc", "on"),
        ])
        with mock.patch.object(provision.utils, "client_from_env",
                               return_value=stub):
            info = provision.get_cluster_info("ASH", "sky-lat-abc")
        self.assertEqual(info.head_instance_id, "srv_a")
        inst, = info.instances["srv_a"]
        self.assertEqual(inst.external_ip, "198.51.100.5")
        self.assertEqual(inst.ssh_port, 22)
        self.assertEqual(info.ssh_user, "ubuntu")


    def test_stop_instances_is_refused_not_terminate(self):
        """STOP is declared unsupported (power_off keeps billing hourly):
        it must raise, never silently DELETE the box — and it must BIND to
        the dispatch contract (CodeRabbit on the PR: the vast-copied
        `(region, cluster_name, ...)` signature TypeErrors at dispatch)."""
        import inspect

        for fn in (provision.stop_instances, provision.terminate_instances):
            sig = inspect.signature(fn)
            self.assertEqual(
                list(sig.parameters),
                ["cluster_name_on_cloud", "provider_config", "worker_only"],
                msg=f"{fn.__name__} must bind the dispatch contract",
            )

        stub = _StubClient(servers=[
            _server_row("srv_a", "sky-lat-abc", "on")
        ])
        with mock.patch.object(provision.utils, "client_from_env",
                               return_value=stub):
            with self.assertRaises(NotImplementedError):
                provision.stop_instances("sky-lat-abc")
        self.assertEqual(stub.deleted, [])

    def test_terminate_refuses_an_empty_cluster_name(self):
        """An empty identity filters NOTHING: list_servers would return the
        whole team's fleet and the loop would delete it (CodeRabbit on the
        PR). Refuse loudly; delete nothing."""
        stub = _StubClient(servers=[
            _server_row("srv_a", "sky-lat-abc", "on")
        ])
        with mock.patch.object(provision.utils, "client_from_env",
                               return_value=stub):
            with self.assertRaises(provision.utils.LatitudeError) as ctx:
                provision.terminate_instances("   ")
        self.assertIn("empty cluster_name_on_cloud", str(ctx.exception))
        self.assertEqual(stub.deleted, [])

    def test_adoption_refuses_off_and_rescue_mode_without_waiting(self):
        """Adopted boxes in `off`/`rescue_mode` can never reach `on` on their
        own and this integration never issues power_on / exit-rescue: fail
        fast instead of blocking the full poll timeout (CodeRabbit on the
        PR). Transitional slugs keep waiting."""
        for status in ("off", "rescue_mode"):
            stub = _StubClient(servers=[
                _server_row("srv_9", "sky-lat-abc", status)
            ])
            with mock.patch.object(provision.utils, "client_from_env",
                                   return_value=stub):
                with self.assertRaises(provision.utils.LatitudeError) as ctx:
                    provision.run_instances(
                        "ASH", "ignored", "sky-lat-abc",
                        _config({"InstanceType": "g4-rtx6kpro-large"}))
            self.assertIn(status, str(ctx.exception))
            self.assertEqual(stub.created_servers, [])

    def test_adoption_still_waits_on_transitional_statuses(self):
        """A mapped transitional adoption (`deploying`) still waits for `on`
        rather than failing fast — only steady dead states refuse."""
        stub = _StubClient(servers=[
            _server_row("srv_9", "sky-lat-abc", "deploying")
        ])
        with mock.patch.object(provision.utils, "client_from_env",
                               return_value=stub):
            record = provision.run_instances(
                "ASH", "ignored", "sky-lat-abc",
                _config({"InstanceType": "g4-rtx6kpro-large"}))
        self.assertEqual(record.head_instance_id, "srv_9")
        self.assertEqual(record.created_instance_ids, [])


class TestCloudWiring(unittest.TestCase):
    """The registration points an out-of-tree/in-tree cloud must not miss —
    each of these was a real lane-killing bug on a previous carry
    ( Shadeform's _REPR, Spheron's auth branch, Vast's template map )."""

    def test_repr_is_the_catalog_name(self):
        from sky.clouds import Latitude
        self.assertEqual(Latitude._REPR, "Latitude")

    def test_all_clouds_contains_latitude(self):
        from sky.skylet import constants
        self.assertIn("latitude", constants.ALL_CLOUDS)

    def test_cluster_config_template_registered(self):
        from sky.backends import cloud_vm_ray_backend
        from sky.clouds import Latitude
        self.assertEqual(
            cloud_vm_ray_backend._get_cluster_config_template(Latitude()),
            "latitude-ray.yml.j2",
        )

    def test_auth_dispatch_covers_latitude(self):
        # _add_auth_to_cluster_config is an isinstance chain ending in
        # `assert False, cloud`; Latitude must take the generic
        # configure_ssh_info branch (its key is registered per-deployment).
        import ast
        import inspect

        from sky.backends import backend_utils
        source = inspect.getsource(
            backend_utils._add_auth_to_cluster_config)
        tree = ast.parse(source)
        names = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        self.assertIn("Latitude", names,
                      "Latitude missing from the auth dispatch chain")

    def test_zones_provision_loop_yields_per_region(self):
        from sky import clouds as clouds_lib
        from sky.clouds import Latitude
        with mock.patch.object(
            Latitude, "regions_with_offering",
            classmethod(lambda cls, *a, **k: [
                clouds_lib.Region("ASH"), clouds_lib.Region("CHI"),
            ]),
        ):
            yields = list(Latitude.zones_provision_loop(
                region="ASH", num_nodes=1, instance_type="g4-rtx6kpro-large"))
        # One yield PER region with an offering — a single yield for "any"
        # turned a first-region failure into the end of provisioning on the
        # Spheron lane.
        self.assertEqual(yields, [None, None])

    def test_ssh_key_file_mount_is_latitude_scoped(self):
        from sky.clouds import Latitude
        mounts = Latitude().get_credential_file_mounts()
        self.assertEqual(mounts, {"~/.latitude/credentials":
                                   "~/.latitude/credentials"})


if __name__ == "__main__":
    unittest.main()
