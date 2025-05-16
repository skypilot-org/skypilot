"""Tests for infra_utils.py"""
import unittest

from sky import clouds
from sky.utils import infra_utils


class TestInfraUtils(unittest.TestCase):
    """Tests for infra_utils.py"""

    def test_from_str(self):
        """Test the from_str function with various inputs."""
        test_cases = [
            # Format: (infra_string, expected_cloud, expected_region, expected_zone)
            ("aws/us-east-1", "AWS", "us-east-1", None),
            ("aws/us-east-1/us-east-1a", "AWS", "us-east-1", "us-east-1a"),
            ("gcp/us-central1", "GCP", "us-central1", None),
            ("k8s/my-cluster-ctx", "Kubernetes", "my-cluster-ctx", None),
            # Test Kubernetes context with slashes
            ("k8s/my/cluster/ctx", "Kubernetes", "my/cluster/ctx", None),
            # Test AWS with empty zone
            ("aws/us-east-1/", "AWS", "us-east-1", None),
            # Test with just cloud
            ("aws", "AWS", None, None),
        ]

        for infra_str, expected_cloud, expected_region, expected_zone in test_cases:
            info = infra_utils.InfraInfo.from_str(infra_str)
            cloud_str = str(info.cloud) if info.cloud else None

            self.assertEqual(
                cloud_str, expected_cloud,
                f"Failed on {infra_str}: Expected cloud={expected_cloud}, got {cloud_str}"
            )
            self.assertEqual(
                info.region, expected_region,
                f"Failed on {infra_str}: Expected region={expected_region}, got {info.region}"
            )
            self.assertEqual(
                info.zone, expected_zone,
                f"Failed on {infra_str}: Expected zone={expected_zone}, got {info.zone}"
            )

    def test_format_infra(self):
        """Test the format_infra function with various inputs."""
        test_cases = [
            # Format: (cloud, region, zone, expected)
            ("aws", "us-east-1", None, "aws/us-east-1"),
            ("aws", "us-east-1", "us-east-1a", "aws/us-east-1/us-east-1a"),
            ("gcp", "us-central1", None, "gcp/us-central1"),
            ("kubernetes", "my-cluster-ctx", None, "kubernetes/my-cluster-ctx"),
            # Test with slashes in Kubernetes context
            ("kubernetes", "my/cluster/ctx", None, "kubernetes/my/cluster/ctx"),
            # Test with zone in Kubernetes
            ("kubernetes", "my-cluster-ctx", "some-zone",
             "kubernetes/my-cluster-ctx/some-zone"),
            # Test with just cloud
            ("aws", None, None, "aws"),
            # Test with None cloud
            (None, "us-east-1", None, None),
            # Additional test cases for simplified implementation
            ("aws", "*", "*", "aws"),
            ("gcp", "us-central1", "*", "gcp/us-central1"),
            ("aws", "*", "us-east-1a", "aws/*/us-east-1a"),
            ("*", "*", "*", None),
        ]

        for cloud, region, zone, expected in test_cases:
            result = infra_utils.format_infra(cloud, region, zone)
            self.assertEqual(result, expected,
                             f"Failed: Expected {expected}, got {result}")


if __name__ == "__main__":
    unittest.main()
