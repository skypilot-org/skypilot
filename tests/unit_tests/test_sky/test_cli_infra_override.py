"""Tests for CLI infrastructure override functionality."""
import unittest
from unittest import mock

from sky.client.cli import command


class TestCliInfraOverride(unittest.TestCase):
    """Tests for CLI infrastructure override functionality."""

    def test_handle_infra_cloud_region_zone_options_basic(self):
        """Test basic infra parsing without wildcards."""
        test_cases = [
            # Format: (infra, cloud, region, zone, expected_cloud, expected_region, expected_zone)
            ('aws', None, None, None, 'aws', '*', '*'),
            ('aws/us-east-1', None, None, None, 'aws', 'us-east-1', '*'),
            ('aws/us-east-1/us-east-1a', None, None, None, 'aws', 'us-east-1',
             'us-east-1a'),
            ('gcp/us-central1', None, None, None, 'gcp', 'us-central1', '*'),
            ('kubernetes/my-cluster', None, None, None, 'kubernetes',
             'my-cluster', '*'),
        ]

        for infra, cloud, region, zone, expected_cloud, expected_region, expected_zone in test_cases:
            with self.subTest(infra=infra):
                result_cloud, result_region, result_zone = command._handle_infra_cloud_region_zone_options(
                    infra, cloud, region, zone)
                self.assertEqual(
                    result_cloud, expected_cloud,
                    f'Cloud mismatch for {infra}: expected {expected_cloud}, got {result_cloud}'
                )
                self.assertEqual(
                    result_region, expected_region,
                    f'Region mismatch for {infra}: expected {expected_region}, got {result_region}'
                )
                self.assertEqual(
                    result_zone, expected_zone,
                    f'Zone mismatch for {infra}: expected {expected_zone}, got {result_zone}'
                )

    def test_handle_infra_cloud_region_zone_options_wildcards(self):
        """Test infra parsing with wildcards - the main bug fix."""
        test_cases = [
            # Format: (infra, expected_cloud, expected_region, expected_zone)
            # These cases test the fix where None values get converted to '*'
            ('gcp/*', 'gcp', '*', '*'),  # Main bug fix case
            ('*/us-west-1', '*', 'us-west-1', '*'),
            ('aws/*/us-east-1a', 'aws', '*', 'us-east-1a'),
            ('*/*/*', '*', '*', '*'),
            ('*', '*', '*', '*'),  # Full wildcard
        ]

        for infra, expected_cloud, expected_region, expected_zone in test_cases:
            with self.subTest(infra=infra):
                result_cloud, result_region, result_zone = command._handle_infra_cloud_region_zone_options(
                    infra, None, None, None)
                self.assertEqual(
                    result_cloud, expected_cloud,
                    f'Cloud mismatch for {infra}: expected {expected_cloud}, got {result_cloud}'
                )
                self.assertEqual(
                    result_region, expected_region,
                    f'Region mismatch for {infra}: expected {expected_region}, got {result_region}'
                )
                self.assertEqual(
                    result_zone, expected_zone,
                    f'Zone mismatch for {infra}: expected {expected_zone}, got {result_zone}'
                )

    def test_handle_infra_cloud_region_zone_options_deprecated_flags(self):
        """Test handling of deprecated --cloud/--region/--zone flags."""
        # When deprecated flags are used without --infra, they should be returned as-is
        result_cloud, result_region, result_zone = command._handle_infra_cloud_region_zone_options(
            None, 'aws', 'us-east-1', 'us-east-1a')
        self.assertEqual(result_cloud, 'aws')
        self.assertEqual(result_region, 'us-east-1')
        self.assertEqual(result_zone, 'us-east-1a')

    def test_handle_infra_cloud_region_zone_options_conflict_error(self):
        """Test that using both --infra and deprecated flags raises an error."""
        with self.assertRaises(ValueError) as context:
            command._handle_infra_cloud_region_zone_options(
                'aws/us-east-1', 'gcp', None, None)
        self.assertIn('Cannot specify both --infra and --cloud',
                      str(context.exception))

    def test_parse_override_params_asterisk_handling(self):
        """Test that asterisk values are properly converted to None in override params."""
        test_cases = [
            # Format: (cloud, region, zone, expected_cloud_none, expected_region_none, expected_zone_none)
            ('*', '*', '*', True, True, True),
            ('none', 'none', 'none', True, True, True),
            ('aws', 'us-east-1', 'us-east-1a', False, False, False),
            ('*', 'us-east-1', '*', True, False, True),
            (None, None, None, None, None, None),  # No override
        ]

        for cloud, region, zone, expected_cloud_none, expected_region_none, expected_zone_none in test_cases:
            with self.subTest(cloud=cloud, region=region, zone=zone):
                # Mock the cloud registry to avoid real cloud object creation
                with mock.patch(
                        'sky.client.cli.command.registry.CLOUD_REGISTRY.from_str'
                ) as mock_from_str:
                    mock_cloud_obj = mock.MagicMock()
                    mock_from_str.return_value = mock_cloud_obj

                    override_params = command._parse_override_params(
                        cloud=cloud, region=region, zone=zone)

                    # Check cloud override
                    if expected_cloud_none is None:
                        self.assertNotIn('cloud', override_params)
                    elif expected_cloud_none:
                        self.assertIsNone(override_params.get('cloud'))
                    else:
                        self.assertEqual(override_params.get('cloud'),
                                         mock_cloud_obj)

                    # Check region override
                    if expected_region_none is None:
                        self.assertNotIn('region', override_params)
                    elif expected_region_none:
                        self.assertIsNone(override_params.get('region'))
                    else:
                        self.assertEqual(override_params.get('region'), region)

                    # Check zone override
                    if expected_zone_none is None:
                        self.assertNotIn('zone', override_params)
                    elif expected_zone_none:
                        self.assertIsNone(override_params.get('zone'))
                    else:
                        self.assertEqual(override_params.get('zone'), zone)

    def test_infra_override_complete_workflow(self):
        """Test the complete workflow: infra parsing -> CLI processing -> override params."""
        # This tests the end-to-end behavior that was broken before the fix
        test_cases = [
            # Format: (infra_str, expected_override_behavior)
            ('gcp/*', {
                'cloud': 'gcp',
                'region_none': True,
                'zone_none': True
            }),
            ('aws/us-east-1', {
                'cloud': 'aws',
                'region': 'us-east-1',
                'zone_none': True
            }),
            ('*/us-west-1', {
                'cloud_none': True,
                'region': 'us-west-1',
                'zone_none': True
            }),
            ('kubernetes', {
                'cloud': 'kubernetes',
                'region_none': True,
                'zone_none': True
            }),
        ]

        for infra_str, expected in test_cases:
            with self.subTest(infra_str=infra_str):
                # Step 1: Parse infra string through CLI handler
                cloud, region, zone = command._handle_infra_cloud_region_zone_options(
                    infra_str, None, None, None)

                # Step 2: Process through override params parser
                with mock.patch(
                        'sky.client.cli.command.registry.CLOUD_REGISTRY.from_str'
                ) as mock_from_str:
                    mock_cloud_obj = mock.MagicMock()
                    mock_from_str.return_value = mock_cloud_obj

                    override_params = command._parse_override_params(
                        cloud=cloud, region=region, zone=zone)

                    # Verify the expected behavior
                    if 'cloud' in expected:
                        # Specific cloud should be set
                        self.assertIn('cloud', override_params)
                        self.assertEqual(mock_from_str.call_args[0][0],
                                         expected['cloud'])
                    elif 'cloud_none' in expected:
                        # Cloud should be None (wildcard)
                        self.assertIn('cloud', override_params)
                        self.assertIsNone(override_params['cloud'])

                    if 'region' in expected:
                        # Specific region should be set
                        self.assertIn('region', override_params)
                        self.assertEqual(override_params['region'],
                                         expected['region'])
                    elif 'region_none' in expected:
                        # Region should be None (wildcard)
                        self.assertIn('region', override_params)
                        self.assertIsNone(override_params['region'])

                    if 'zone' in expected:
                        # Specific zone should be set
                        self.assertIn('zone', override_params)
                        self.assertEqual(override_params['zone'],
                                         expected['zone'])
                    elif 'zone_none' in expected:
                        # Zone should be None (wildcard)
                        self.assertIn('zone', override_params)
                        self.assertIsNone(override_params['zone'])

    def test_edge_cases(self):
        """Test edge cases and error conditions."""
        # Test empty infra string
        cloud, region, zone = command._handle_infra_cloud_region_zone_options(
            '', None, None, None)
        # Empty string should result in all wildcards due to InfraInfo returning all None
        self.assertEqual(cloud, '*')
        self.assertEqual(region, '*')
        self.assertEqual(zone, '*')

    def test_kubernetes_special_handling(self):
        """Test that Kubernetes contexts with slashes are handled correctly."""
        test_cases = [
            ('k8s/my-cluster', 'kubernetes', 'my-cluster', '*'),
            ('kubernetes/my/complex/cluster/name', 'kubernetes',
             'my/complex/cluster/name', '*'),
            ('k8s', 'kubernetes', '*', '*'),  # Just k8s without context
        ]

        for infra_str, expected_cloud, expected_region, expected_zone in test_cases:
            with self.subTest(infra_str=infra_str):
                cloud, region, zone = command._handle_infra_cloud_region_zone_options(
                    infra_str, None, None, None)
                self.assertEqual(cloud, expected_cloud)
                self.assertEqual(region, expected_region)
                self.assertEqual(zone, expected_zone)

    def test_reviewer_specific_scenario(self):
        """Test the exact scenario mentioned by the reviewer in the PR comment."""
        # Scenario: sky launch region.yaml --infra gcp/*
        # where region.yaml specifies region: us-east-1
        # This should NOT raise "Invalid region 'us-east-1'" error

        infra_str = 'gcp/*'

        # Step 1: CLI processing should convert gcp/* to proper parameters
        cloud, region, zone = command._handle_infra_cloud_region_zone_options(
            infra_str, None, None, None)

        # Should get: cloud='gcp', region='*', zone='*'
        self.assertEqual(cloud, 'gcp')
        self.assertEqual(region, '*')
        self.assertEqual(zone, '*')

        # Step 2: Override params should convert '*' to None for proper override
        with mock.patch(
                'sky.client.cli.command.registry.CLOUD_REGISTRY.from_str'
        ) as mock_from_str:
            mock_gcp_obj = mock.MagicMock()
            mock_from_str.return_value = mock_gcp_obj

            override_params = command._parse_override_params(cloud=cloud,
                                                             region=region,
                                                             zone=zone)

            # Should get: {'cloud': gcp_obj, 'region': None, 'zone': None}
            self.assertIn('cloud', override_params)
            self.assertEqual(override_params['cloud'], mock_gcp_obj)
            self.assertIn('region', override_params)
            self.assertIsNone(
                override_params['region'])  # None means "force override"
            self.assertIn('zone', override_params)
            self.assertIsNone(
                override_params['zone'])  # None means "force override"

        # This ensures that when Resources.copy(**override_params) is called,
        # it will override the YAML's region='us-east-1' with None,
        # effectively removing the region constraint and allowing any GCP region.


if __name__ == '__main__':
    unittest.main()
