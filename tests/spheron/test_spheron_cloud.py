"""Behavioral regression tests for CodeRabbit review findings on Spheron.

These exercise actual method calls with mocked catalog data — not
source-text inspection — so a regression fails with a behavior mismatch.
"""
import unittest
from unittest import mock

from sky.clouds import spheron


class TestRegionsWithOfferingAcceptsResources(unittest.TestCase):
    def test_accepts_resources_kwarg(self):
        with mock.patch.object(
            spheron.spheron_catalog,
            'get_region_zones_for_instance_type',
            return_value=[],
        ):
            result = spheron.Spheron.regions_with_offering(
                'gpu', None, False, None, None, resources=object(),
            )
        self.assertEqual(result, [])

    def test_without_resources_kwarg(self):
        with mock.patch.object(
            spheron.spheron_catalog,
            'get_region_zones_for_instance_type',
            return_value=[],
        ):
            result = spheron.Spheron.regions_with_offering(
                'gpu', None, False, None, None,
            )
        self.assertEqual(result, [])


class TestZonesProvisionLoop(unittest.TestCase):
    def _run(self, region='us-east', regions=None):
        with mock.patch.object(
            spheron.Spheron, 'regions_with_offering', return_value=regions or [],
        ) as m:
            result = list(spheron.Spheron.zones_provision_loop(
                region=region, num_nodes=1,
                instance_type='gpu', accelerators=None, use_spot=False,
            ))
        return result, m

    def test_no_offering_yields_nothing(self):
        result, _ = self._run(regions=[])
        self.assertEqual(result, [])

    def test_one_region_yields_one(self):
        from sky import clouds as clouds_lib
        result, _ = self._run(regions=[clouds_lib.Region('us-east')])
        self.assertEqual(result, [None])

    def test_two_regions_yield_two(self):
        """Distinguishes the iteration fix from the single-yield shortcut:
        a list of two regions must yield [None, None], not [None]."""
        from sky import clouds as clouds_lib
        result, _ = self._run(regions=[
            clouds_lib.Region('us-east'),
            clouds_lib.Region('eu-west'),
        ])
        self.assertEqual(result, [None, None])

    def test_requested_region_is_forwarded(self):
        _, m = self._run(region='us-west')
        self.assertEqual(m.call_args.kwargs.get('region'), 'us-west')

    def test_unavailable_region_yields_nothing(self):
        result, _ = self._run(region='eu-west', regions=[])
        self.assertEqual(result, [])


def _fake_client(status='STOPPED'):
    class _Client:
        def list_deployments(self, **kwargs):
            return [{
                'id': 'deploy-1',
                'name': 'test-cluster',  # matches cluster_name_on_cloud
                'status': status,
            }]
    return _Client()


class TestQueryInstancesKeepsStopped(unittest.TestCase):
    def test_stopped_visible_with_non_terminated_only_true(self):
        from sky.provision.spheron import instance as inst
        from sky.utils import status_lib
        with mock.patch.object(
            inst, '_client', return_value=_fake_client('STOPPED'),
        ):
            result = inst.query_instances(
                'test-cluster', 'test-cluster',
                non_terminated_only=True,
            )
        self.assertIn('deploy-1', result)
        self.assertEqual(
            result['deploy-1'][0], status_lib.ClusterStatus.STOPPED,
        )

    def test_stopped_visible_with_non_terminated_only_false(self):
        from sky.provision.spheron import instance as inst
        from sky.utils import status_lib
        with mock.patch.object(
            inst, '_client', return_value=_fake_client('STOPPED'),
        ):
            result = inst.query_instances(
                'test-cluster', 'test-cluster',
                non_terminated_only=False,
            )
        self.assertIn('deploy-1', result)
        self.assertEqual(
            result['deploy-1'][0], status_lib.ClusterStatus.STOPPED,
        )


if __name__ == '__main__':
    unittest.main()
