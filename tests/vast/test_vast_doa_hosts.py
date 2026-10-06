"""The launch path must refuse offers from dead-on-arrival Vast hosts.

ENG-493 runs 3-4 (2026-10-02): machine 152941 -- the only host behind the
RTX PRO 6000 WS bucket -- took a rental and went 'offline' with the port
mapping gone TWICE before any bootstrap stage stamped, so renting it again
burns paid time on a host that can never boot. The controller prices
catalog BUCKETS (one row per gpu/count/ram bundle) and cannot see machine
ids, so the bucket refusal it carried before blocked every future healthy
host in the bundle; the blocklist therefore lives HERE, at the only layer
that sees the host, and stays host-scoped.

Fail-closed contract: a bucket whose only offers are dead hosts refuses
('could not find an offer') rather than renting one; a healthy host in the
same bucket still launches.
"""
import unittest
from unittest import mock

from sky.adaptors import vast
from sky.provision.vast import utils


def _offer(offer_id, machine_id, gpu_name='RTX PRO 6000 WS', price=1.60):
    return {
        'id': offer_id,
        'machine_id': machine_id,
        'gpu_name': gpu_name,
        'num_gpus': 1,
        'cpu_cores': 32,
        'cpu_ram': 65536,
        'search': {
            'totalHour': price
        },
        'dph_total': price,
        'geolocation': 'Czechia, CZ, EU',
        'hosting_type': 1,
    }


def _launch(client):
    with mock.patch.object(utils, 'vast') as vast_mod:
        vast_mod.vast.return_value = client
        return utils.launch(
            name='test-cluster',
            instance_type='1x-RTX_PRO_6000_WS-32-65536',
            region='EU',
            disk_size=256,
            image_name='docker.io/vastai/kvm',
            ports=[22],
            preemptible=False,
            secure_only=True,
        )


class TestDeadOnArrivalHosts(unittest.TestCase):
    """launch() drops offers from _DEAD_ON_ARRIVAL_HOSTS before choosing."""

    def test_the_dead_host_is_in_the_blocklist(self):
        # The pin: machine 152941 (ENG-493 runs 3-4). A test that fails
        # after an edit REMOVED the id without a measured healthy relist
        # is doing its job.
        self.assertEqual(utils._DEAD_ON_ARRIVAL_HOSTS, frozenset({152941}))

    def test_launch_skips_the_dead_host_and_rents_the_healthy_one(self):
        client = mock.Mock()
        client.search_offers.return_value = [
            _offer(111, 152941),  # the DOA host, listed first
            _offer(222, 999999),  # a healthy host in the same bucket
        ]
        client.create_instance.return_value = {'new_contract': 'inst-ok'}
        client.show_instance.return_value = {'id': 'vast-53903136'}

        result = _launch(client)

        self.assertEqual(result, 'vast-53903136')
        self.assertEqual(client.create_instance.call_count, 1)
        self.assertEqual(client.create_instance.call_args.kwargs['id'], 222)

    def test_a_bucket_whose_only_offer_is_the_dead_host_refuses(self):
        client = mock.Mock()
        client.search_offers.return_value = [_offer(111, 152941)]

        with self.assertRaisesRegex(RuntimeError, 'could not find an offer'):
            _launch(client)

        client.create_instance.assert_not_called()

    def test_offers_without_a_machine_id_still_launch(self):
        # search_offers rows the API returns without a machine_id key must
        # not be dropped by the blocklist filter (absent id != dead id).
        client = mock.Mock()
        client.search_offers.return_value = [
            {
                'id': 333,
                'gpu_name': 'RTX PRO 6000 WS',
                'num_gpus': 1,
                'cpu_cores': 32,
                'cpu_ram': 65536,
                'dph_total': 1.60,
                'geolocation': 'Czechia, CZ, EU',
                'hosting_type': 1
            },
        ]
        client.create_instance.return_value = {'new_contract': 'inst-ok'}
        client.show_instance.return_value = {'id': 'vast-53903136'}

        result = _launch(client)

        self.assertEqual(result, 'vast-53903136')
        self.assertEqual(client.create_instance.call_args.kwargs['id'], 333)


if __name__ == '__main__':
    unittest.main()
