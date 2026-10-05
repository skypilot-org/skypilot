"""Vast onstart AND file mounts must NEVER carry the account API key.

Vast hosts are third-party marketplace machines. Upstream leaked the master
key to the node in TWO places:

  1. sky/provision/vast/utils.py — onstart ``echo <key> > ~/.vast_api_key``
  2. sky/clouds/vast.py — ``get_credential_file_mounts`` returned
     {~/.config/vastai/vast_api_key: ...}, which SkyPilot's file-sync copies
     to every node

Both are removed in this fork. Every lifecycle call (create/stop/destroy)
runs controller-side via ``vast.vast().<api>``; nothing on the node needs the
key. The companion contract on the controller side is the S07/S10
secret-isolation pattern (skypilot-controller): secrets reach claims only as
same-namespace SecretKeyRefs — a key on the NODE would bypass that entirely.
"""

import inspect
import unittest
from unittest import mock

from sky.provision.vast import utils


class TestVastOnstartNeverWritesApiKey(unittest.TestCase):
    """The submitted onstart command must not contain the account key."""

    def test_launch_onstart_excludes_api_key(self):
        """Mock the Vast SDK and assert the submitted onstart_cmd is key-free.

        The key would flow through `vast.vast().client.api_key` — mock it to
        a recognizable sentinel and assert that sentinel never reaches the
        `create_instance` call's onstart_cmd.

        instance_type is a SkyPilot invention:
        '{n}x-{gpu_name}-{cpu_cores}-{cpu_ram}' (fetch_vast.create_instance_type).
        '1x-RTX_6000D-32-65536' parses as 1 GPU, "RTX 6000D", 65536 MB CPU RAM.
        """
        sentinel = 'sk-vast-SECRET-KEY-should-never-appear'
        fake_client = mock.Mock()
        fake_client.client.api_key = sentinel

        fake_offer = {
            'id': 12345,
            'gpu_name': 'RTX 6000D',
            'num_gpus': 1,
            'cpu_ram': 65536,
            'geolocation': 'Czechia, CZ, EU',
            'dph_total': 1.50,
            'min_bid': 1.50,
        }

        captured_launch_params = {}

        def fake_create_instance(**kwargs):
            captured_launch_params.update(kwargs)
            return {'new_contract': 'fake-contract'}

        def fake_show_instance(id=None):
            return {'id': 'fake-instance-id'}

        def fake_search_offers(query=None, **kwargs):
            return [fake_offer]

        fake_client.create_instance.side_effect = fake_create_instance
        fake_client.show_instance.side_effect = fake_show_instance
        fake_client.search_offers.side_effect = fake_search_offers

        with mock.patch.object(utils, 'vast') as vast_mod:
            vast_mod.vast.return_value = fake_client
            utils.launch(
                name='test-cluster',
                instance_type='1x-RTX_6000D-32-65536',
                region='EU',
                disk_size=64,
                image_name='nvidia/cuda',
                ports=[22],
                preemptible=False,
                secure_only=True,
                create_instance_kwargs={'onstart_cmd': 'echo hello'},
            )

        onstart = captured_launch_params.get('onstart_cmd', '')
        self.assertNotIn(sentinel, onstart)
        self.assertNotIn('vast_api_key', onstart)
        self.assertIn('no_auto_tmux', onstart, 'sanity: the legitimate onstart prefix survived')

    def test_utils_source_contains_no_key_write(self):
        """Static guard: the literal ~/.vast_api_key write is gone from source.

        A future merge from upstream that reintroduces the write must fail
        this test even if the dynamic test above is mocked around.
        """
        source = inspect.getsource(utils)
        self.assertNotIn('.vast_api_key', source)

