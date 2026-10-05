"""The Blackwell RTX 6000 family must stay one catalog accelerator end to end.

Measured 2026-09-30 on the live Vast market: the only Secure-Cloud on-demand
KVM RTX-6000-class offer flipped from an 'RTX 6000D' to an 'RTX PRO 6000
Max-Q' within hours. Two defects made the lane chase that flip:

  * fetch_vast filed the 6000D under RTX6000 (next to the 24GB Turing Quadro)
    and the PRO variants under RTXPRO6000, so no single accelerator covered
    the family.
  * utils.launch split the instance type on '-', truncating the gpu_name
    'RTX PRO 6000 Max-Q' to 'RTX PRO 6000 Max', which the API exact-matches
    against nothing.
"""
import contextlib
import csv
import io
import os
import runpy
import tempfile
import unittest
from unittest import mock

from sky.adaptors import vast
from sky.provision.vast import utils


def _offer(gpu_name, cpu_cores=96, cpu_ram=96690, price=1.74):
    return {
        'gpu_name': gpu_name,
        'num_gpus': 1,
        'cpu_cores': cpu_cores,
        'cpu_ram': cpu_ram,
        'gpu_total_ram': 97887,
        'search': {'totalHour': price},
        'min_bid': price,
        'geolocation': 'Czechia, CZ, EU',
        'hosting_type': 1,
    }


def _fetch(offers):
    vast.vast()  # triggers import + patch
    with mock.patch.object(vast, 'vast') as mock_vast:
        mock_vast.return_value.search_offers.return_value = offers
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    runpy.run_module('sky.catalog.data_fetchers.fetch_vast',
                                     run_name='__main__')
                with open(os.path.join(tmpdir, 'vast', 'vms.csv')) as f:
                    return list(csv.DictReader(f))
            finally:
                os.chdir(old_cwd)


class TestRtx6000FamilyCatalog(unittest.TestCase):

    def test_blackwell_variants_share_one_accelerator(self):
        rows = _fetch([
            _offer('RTX 6000D', cpu_cores=32, cpu_ram=65536, price=1.47),
            _offer('RTX PRO 6000 Max-Q'),
            _offer('RTX PRO 6000 S', cpu_cores=48),
            _offer('RTX PRO 6000 WS', cpu_cores=64),
        ])
        by_type = {r['InstanceType']: r['AcceleratorName'] for r in rows}
        self.assertEqual(
            by_type, {
                '1x-RTX_6000D-32-65536': 'RTXPRO6000',
                '1x-RTX_PRO_6000_Max-Q-96-96690': 'RTXPRO6000',
                '1x-RTX_PRO_6000_S-48-96690': 'RTXPRO6000',
                '1x-RTX_PRO_6000_WS-64-96690': 'RTXPRO6000',
            })

    def test_turing_quadro_stays_rtx6000(self):
        rows = _fetch([_offer('Q RTX 6000', cpu_cores=16, price=0.5)])
        self.assertEqual([r['AcceleratorName'] for r in rows], ['RTX6000'])


class TestLaunchQueryGpuName(unittest.TestCase):

    def _launch_query(self, instance_type):
        queries = []
        fake_client = mock.Mock()

        def fake_search_offers(query=None, **kwargs):
            queries.append(query)
            return []

        fake_client.search_offers.side_effect = fake_search_offers
        with mock.patch.object(utils, 'vast') as vast_mod:
            vast_mod.vast.return_value = fake_client
            with self.assertRaises(RuntimeError):
                utils.launch(name='c',
                             instance_type=instance_type,
                             region='EU',
                             disk_size=256,
                             image_name='vastai/base:0.0.2',
                             ports=[22],
                             preemptible=False,
                             secure_only=True)
        self.assertEqual(len(queries), 1)
        return queries[0]

    def test_hyphenated_gpu_name_survives_parse(self):
        query = self._launch_query('1x-RTX_PRO_6000_Max-Q-96-96690')
        self.assertIn('gpu_name="RTX PRO 6000 Max-Q"', query)
        self.assertIn('num_gpus=1', query)
        # SDK units: cpu_ram in GB (instance type's 96690 MB -> ~94.4 GB),
        # duration in days. Raw API units here matched nothing live.
        self.assertIn('cpu_ram>=94.4238', query)
        self.assertIn('duration>=3 ', query)
        self.assertNotIn('cpu_ram>=65536', query)
        self.assertNotIn('duration>=259200', query)

    def test_plain_gpu_name_unchanged(self):
        query = self._launch_query('1x-RTX_6000D-32-65536')
        self.assertIn('gpu_name="RTX 6000D"', query)


class TestSearchDropsTheSdkVerifiedDefault(unittest.TestCase):
    """The SDK's implicit verified=true filter hides Secure-Cloud offers
    (verified=None on every live RTX PRO 6000 Max-Q, 2026-10-01); both the
    catalog fetch and the launch search must opt out of it."""

    def test_launch_search_passes_no_default_with_base_terms(self):
        calls = []
        fake_client = mock.Mock()

        def fake_search_offers(query=None, **kwargs):
            calls.append((query, kwargs))
            return []

        fake_client.search_offers.side_effect = fake_search_offers
        with mock.patch.object(utils, 'vast') as vast_mod:
            vast_mod.vast.return_value = fake_client
            with self.assertRaises(RuntimeError):
                utils.launch(name='c',
                             instance_type='1x-RTX_6000D-32-65536',
                             region='EU',
                             disk_size=256,
                             image_name='vastai/base:0.0.2',
                             ports=[22],
                             preemptible=False,
                             secure_only=True)
        query, kwargs = calls[0]
        self.assertIs(kwargs.get('no_default'), True)
        self.assertTrue(query.startswith(utils.SEARCH_BASE_TERMS), query)

    def test_catalog_fetch_passes_no_default_with_base_terms(self):
        vast.vast()
        with mock.patch.object(vast, 'vast') as mock_vast:
            mock_vast.return_value.search_offers.return_value = []
            with tempfile.TemporaryDirectory() as tmpdir:
                old_cwd = os.getcwd()
                os.chdir(tmpdir)
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        runpy.run_module(
                            'sky.catalog.data_fetchers.fetch_vast',
                            run_name='__main__')
                finally:
                    os.chdir(old_cwd)
            call = mock_vast.return_value.search_offers.call_args
        self.assertIs(call.kwargs.get('no_default'), True)
        self.assertTrue(
            call.kwargs['query'].startswith(utils.SEARCH_BASE_TERMS),
            call.kwargs['query'])


if __name__ == '__main__':
    unittest.main()
