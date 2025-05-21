"""Tests for infra_utils.py"""
import unittest

from sky.utils import infra_utils


class TestInfraUtils(unittest.TestCase):
    """Tests for infra_utils.py"""

    def test_from_str(self):
        """Test the from_str function with various inputs."""
        test_cases = [
            # Format: (infra_str, expected_cloud, expected_region, expected_zone)
            ('aws/us-east-1', 'aws', 'us-east-1', None),
            ('aws/us-east-1/us-east-1a', 'aws', 'us-east-1', 'us-east-1a'),
            ('gcp/us-central1', 'gcp', 'us-central1', None),
            ('k8s/my-cluster-ctx', 'kubernetes', 'my-cluster-ctx', None),
            ('kubernetes/my-cluster-ctx', 'kubernetes', 'my-cluster-ctx', None),
            # Test Kubernetes context with slashes
            ('k8s/my/cluster/ctx', 'kubernetes', 'my/cluster/ctx', None),
            # Test AWS with empty zone
            ('aws/us-east-1/', 'aws', 'us-east-1', None),
            # Test with just cloud
            ('aws', 'aws', None, None),
            # Test with asterisk
            ('*/us-east-1', None, 'us-east-1', None),
            ('aws/*/us-east-1a', 'aws', None, 'us-east-1a'),
            ('aws/*', 'aws', None, None),
            ('*/*/us-east-1a', None, None, 'us-east-1a'),
            (None, None, None, None),
            ('*', None, None, None),
            # Test case sensitivity
            ('AWS/US-EAST-1', 'aws', 'US-EAST-1', None),
            ('GCP/US-CENTRAL1', 'gcp', 'US-CENTRAL1', None),
            ('K8S/MY-CLUSTER', 'kubernetes', 'MY-CLUSTER', None),
            # Test whitespace handling
            ('  aws/us-east-1  ', 'aws', 'us-east-1', None),
            (' aws / us-east-1 / us-east-1a ', 'aws', 'us-east-1',
             'us-east-1a'),
            # Test local and lambda clouds
            ('local', 'local', None, None),
            ('lambda', 'lambda', None, None),
        ]

        for infra_str, expected_cloud, expected_region, expected_zone in test_cases:
            info = infra_utils.InfraInfo.from_str(infra_str)
            cloud_str = info.cloud

            self.assertEqual(
                cloud_str, expected_cloud,
                f'Failed on {infra_str}: Expected cloud={expected_cloud}, got {cloud_str}'
            )
            self.assertEqual(
                info.region, expected_region,
                f'Failed on {infra_str}: Expected region={expected_region}, got {info.region}'
            )
            self.assertEqual(
                info.zone, expected_zone,
                f'Failed on {infra_str}: Expected zone={expected_zone}, got {info.zone}'
            )

    def test_from_str_errors(self):
        """Test the from_str function with invalid inputs."""
        error_test_cases = [
            # Too many segments
            'aws/us-east-1/us-east-1a/extra',
            # Invalid format
            'aws//us-east-1',
            # Just slashes
            '///',
            # Multiple consecutive slashes
            'aws///us-east-1',
        ]

        for infra_str in error_test_cases:
            with self.assertRaises((ValueError, TypeError),
                                   msg=f'Expected error for {infra_str!r}'):
                infra_utils.InfraInfo.from_str(infra_str)

    def test_to_str(self):
        """Test the to_str function with various inputs."""
        test_cases = [
            # Format: (cloud, region, zone, expected)
            ('aws', 'us-east-1', None, 'aws/us-east-1'),
            ('aws', 'us-east-1', 'us-east-1a', 'aws/us-east-1/us-east-1a'),
            ('gcp', 'us-central1', None, 'gcp/us-central1'),
            ('kubernetes', 'my-cluster-ctx', None, 'kubernetes/my-cluster-ctx'),
            # Test with slashes in Kubernetes context
            ('kubernetes', 'my/cluster/ctx', None, 'kubernetes/my/cluster/ctx'),
            # Test with zone in Kubernetes
            ('kubernetes', 'my-cluster-ctx', 'some-zone',
             'kubernetes/my-cluster-ctx/some-zone'),
            # Test with just cloud
            ('aws', None, None, 'aws'),
            # Test with None cloud
            (None, 'us-east-1', None, '*/us-east-1'),
            # Additional test cases for simplified implementation
            ('aws', '*', '*', 'aws'),
            ('gcp', 'us-central1', '*', 'gcp/us-central1'),
            ('aws', '*', 'us-east-1a', 'aws/*/us-east-1a'),
            (None, None, None, None),
            ('*', '*', '*', None),
            ('*', 'us-east-1', None, '*/us-east-1'),
            # Test case sensitivity preservation
            ('aws', 'US-EAST-1', 'US-EAST-1A', 'aws/US-EAST-1/US-EAST-1A'),
            # Test local and lambda clouds
            ('local', None, None, 'local'),
            ('lambda', 'region-name', None, 'lambda/region-name'),
        ]

        for cloud, region, zone, expected in test_cases:
            result = infra_utils.InfraInfo(cloud, region, zone).to_str()
            self.assertEqual(result, expected,
                             f'Failed: Expected {expected}, got {result}')

    def test_formatted_str(self):
        """Test the formatted_str function with various inputs."""
        test_cases = [
            # Format: (cloud, region, zone, truncate, expected)
            ('aws', 'us-east-1', None, True, 'aws (us-east-1)'),
            ('aws', 'us-east-1', 'us-east-1a', True, 'aws (us-east-1a)'),
            ('gcp', 'us-central1', None, True, 'gcp (us-central1)'),
            ('kubernetes', 'my-cluster-ctx', None, True,
             'kubernetes (my-cluster-ctx)'),
            # Test with slashes in Kubernetes context
            ('kubernetes', 'my/cluster/ctx', None, True,
             'kubernetes (my/cluster/ctx)'),
            # Test with just cloud
            ('aws', None, None, True, 'aws'),
            # Test with None cloud
            (None, 'us-east-1', None, True, '-'),
            # Test with long region/zone (truncation)
            ('aws', 'us-east-1-very-long-region', None, True,
             'aws (us-east-1-v...long-region)'),
            ('aws', 'us-east-1-very-very-very-long-region', None, True,
             'aws (us-east-1-v...long-region)'),
            ('aws', 'us-east-1-very-long-region', None, False,
             'aws (us-east-1-very-long-region)'),
            # Test with asterisk
            ('*', '*', '*', True, '-'),
            ('aws', '*', '*', True, 'aws'),
            ('aws', '*', 'us-east-1a', True, 'aws (us-east-1a)'),
            ('*', 'us-east-1', None, True, '-'),
            # Test truncation boundary cases
            ('aws', 'x' * 25, None, True, 'aws (' + 'x' * 25 + ')'),
            ('aws', 'x' * 26, None, True,
             'aws (' + 'x' * 11 + '...' + 'x' * 11 + ')'),
            ('aws', 'x' * 24, None, True, 'aws (' + 'x' * 24 + ')'),
            # Test with empty strings
            ('aws', '', None, True, 'aws'),
            ('aws', '', '', True, 'aws'),
            # Test local and lambda clouds
            ('local', None, None, True, 'local'),
            ('lambda', 'region-name', None, True, 'lambda (region-name)'),
        ]

        for cloud, region, zone, truncate, expected in test_cases:
            result = infra_utils.InfraInfo(
                cloud, region, zone).formatted_str(truncate=truncate)
            self.assertEqual(
                result, expected, f'Failed: Expected {expected}, got {result}, '
                f'cloud={cloud}, region={region}, zone={zone}, '
                f'truncate={truncate}')
