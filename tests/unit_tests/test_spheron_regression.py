"""Regression tests for the four CodeRabbit review findings on Spheron."""
import inspect
import unittest
from unittest import mock


class TestRegionsWithOfferingAcceptsResources(unittest.TestCase):
    def test_accepts_resources_kwarg(self):
        from sky.clouds import spheron
        sig = inspect.signature(spheron.Spheron.regions_with_offering)
        self.assertIn('resources', sig.parameters)


class TestZonesProvisionLoopHonorsNoOffering(unittest.TestCase):
    def test_no_offering_yields_nothing(self):
        from sky.clouds import spheron
        with mock.patch.object(spheron.Spheron, 'regions_with_offering',
                                return_value=[]):
            result = list(spheron.Spheron.zones_provision_loop(
                region='region', num_nodes=1,
                instance_type='instance', accelerators=None,
                use_spot=False))
        self.assertEqual(result, [])


class TestQueryInstancesKeepsStopped(unittest.TestCase):
    def test_stopped_deployment_is_returned_even_when_non_terminated_only(self):
        """BEHAVIOURAL, not a source-text grep.

        The previous version asserted a filter expression was absent from
        `inspect.getsource(...)`, which passes just as happily if the function
        is deleted, renamed, or rewritten to drop STOPPED some other way. This
        drives the real call and asserts the STOPPED deployment comes back,
        which is the property that matters: a stopped deployment still holds
        (and bills for) its disks, so a caller reporting or reaping capacity
        must be able to see it.
        """
        from sky.provision.spheron import instance
        from sky.utils import status_lib

        deployments = [
            {'id': 'dep-stopped', 'status': 'stopped'},
            {'id': 'dep-running', 'status': 'running'},
            {'id': 'dep-failed', 'status': 'failed'},
        ]
        with mock.patch.object(instance, '_client', return_value=mock.MagicMock()), \
             mock.patch.object(instance, '_deployments_for_cluster',
                               return_value=deployments):
            got = instance.query_instances('c', 'c-on-cloud',
                                           non_terminated_only=True)

        self.assertEqual(got['dep-stopped'][0], status_lib.ClusterStatus.STOPPED)
        self.assertEqual(got['dep-running'][0], status_lib.ClusterStatus.UP)
        # A genuinely GONE deployment is still excluded, by the `is None`
        # branch -- so this never returns a terminated instance regardless.
        self.assertNotIn('dep-failed', got)


if __name__ == '__main__':
    unittest.main()


class TestPickOsFallsBackInsteadOfRaising(unittest.TestCase):
    """A GPU offer with only a plain Ubuntu image must still be provisionable.

    Spheron's massed-compute GPU offers list exactly one OS and no cuda-named
    image, so a hard `pick_os(["cuda"])` requirement made EVERY Spheron GPU
    offer unprovisionable — verified live: a launch reached the provisioner and
    died with

        SpheronError: offer 'gpu_1x_pro_6000_blackwell_us-central-9' has no OS
        matching ['cuda']; available: ['Ubuntu Server 22.04']

    The CUDA-less risk is covered for free one layer down by urun's bootstrap
    `runtime-gpu-probe` ($0 `nvidia-smi -L`, refuses before the paid boot).
    """

    def _offer(self, os_options):
        from sky.adaptors import spheron
        return spheron.Offer(
            provider="massed-compute",
            offer_id="gpu_1x_pro_6000_blackwell_us-central-9",
            gpu_type="RTXPRO6000-PCIE",
            gpu_count=1,
            gpu_memory_gb=96,
            vcpus=16.0,
            memory_gb=144.0,
            price_per_hour_usd=2.39,
            regions=["us-central-9"],
            os_options=list(os_options),
            instance_type="massed-compute_gpu_1x_pro_6000_blackwell_us-central-9",
            supports_cloud_init=True,
            maintenance=False,
            minimum_runtime_minutes=None,
        )

    def test_prefers_a_cuda_image_when_one_exists(self):
        from sky.adaptors import spheron
        o = self._offer(["Ubuntu Server 22.04", "Ubuntu 22.04 CUDA 12.4"])
        self.assertIn("CUDA", o.pick_os(["cuda"]))

    def test_strict_by_default_still_raises(self):
        """The DEFAULT stays strict. Some Spheron providers do ship cuda images
        (sesterce lists "Ubuntu Server 22.04 LTS R570 CUDA 12.8"), so a blanket
        fallback would hide a genuinely wrong preference -- and would break
        skypilot-controller's runner/test_spheron_api.py, which asserts that
        asking for an absent OS raises."""
        from sky.adaptors import spheron
        o = self._offer(["Ubuntu Server 22.04"])
        with self.assertRaises(spheron.SpheronError):
            o.pick_os(["cuda"])

    def test_required_false_falls_back_to_the_only_os(self):
        """The ONE caller that must tolerate absence: massed-compute GPU offers
        list only "Ubuntu Server 22.04", which made every such offer
        unprovisionable. Safe because the bootstrap's $0 runtime-gpu-probe
        gates a driverless image before any paid boot."""
        from sky.adaptors import spheron
        o = self._offer(["Ubuntu Server 22.04"])
        self.assertEqual(o.pick_os(["cuda"], required=False), "Ubuntu Server 22.04")

    def test_required_false_still_prefers_cuda_when_offered(self):
        from sky.adaptors import spheron
        o = self._offer(["AlmaLinux 9 Plain", "Ubuntu Server 22.04 LTS R570 CUDA 12.8"])
        self.assertIn("CUDA", o.pick_os(["cuda"], required=False))

    def test_still_fails_hard_when_the_offer_lists_no_os_at_all(self):
        from sky.adaptors import spheron
        o = self._offer([])
        with self.assertRaises(spheron.SpheronError):
            o.pick_os(["cuda"])


class TestEnsureKeyHasNoSilentFallback(unittest.TestCase):
    """`_ensure_key` must trust ONLY the substituted `node_config['PublicKey']`.

    MEASURED on dev-usw2 2026-09-24: the controller's `~/.ssh/sky-key.pub` does
    not exist (SkyPilot 0.13 keeps keys under `~/.sky/clients/<hash>/ssh/`), so
    the old fallback was dead code that could only ever mask a substitution
    failure. The placeholder case is the real hazard: it is TRUTHY, so it slips
    past a bare falsiness check and fails later at the API, far from the cause.
    """

    def _config(self, public_key):
        return mock.Mock(node_config={'PublicKey': public_key})

    def test_substituted_key_is_forwarded(self):
        from sky.provision.spheron import instance
        client = mock.Mock()
        client.ensure_ssh_key.return_value = 'key-id'
        result = instance._ensure_key(client, self._config('ssh-rsa AAAAB3Nza real'))
        self.assertEqual(result, 'key-id')
        client.ensure_ssh_key.assert_called_once_with('skypilot',
                                                      'ssh-rsa AAAAB3Nza real')

    def test_unsubstituted_placeholder_is_rejected(self):
        """The whole point: the literal placeholder must NOT be uploaded."""
        from sky.adaptors import spheron
        from sky.provision.spheron import instance
        client = mock.Mock()
        with self.assertRaises(spheron.SpheronError):
            instance._ensure_key(
                client, self._config(instance._SSH_PUBLIC_KEY_PLACEHOLDER))
        client.ensure_ssh_key.assert_not_called()

    def test_missing_key_is_rejected(self):
        from sky.adaptors import spheron
        from sky.provision.spheron import instance
        client = mock.Mock()
        for value in (None, '', '   '):
            with self.assertRaises(spheron.SpheronError):
                instance._ensure_key(client, self._config(value))
        client.ensure_ssh_key.assert_not_called()

    def test_ssh_dir_is_never_consulted(self):
        """Even with a readable ~/.ssh/sky-key.pub, absence must still fail."""
        from sky.adaptors import spheron
        from sky.provision.spheron import instance
        client = mock.Mock()
        opener = mock.mock_open(read_data='ssh-rsa STALE-DISK-KEY')
        with mock.patch('os.path.exists', return_value=True), \
             mock.patch('builtins.open', opener):
            with self.assertRaises(spheron.SpheronError):
                instance._ensure_key(client, self._config(None))
        opener.assert_not_called()
        client.ensure_ssh_key.assert_not_called()
