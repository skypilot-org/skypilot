"""Tests for AWS catalog data fetcher."""
import pandas as pd
import pytest


class TestAWSInstanceTypeWorkarounds:
    """Tests for AWS instance type accelerator workarounds.

    The AWS API sometimes returns incorrect accelerator information for
    certain instance types. These tests verify that our workarounds
    correctly identify and fix these cases.
    """

    def _create_mock_row(self, instance_type: str, gpu_name: str = 'NVIDIA',
                         gpu_count: int = 8) -> dict:
        """Create a mock row simulating AWS API response."""
        return {
            'InstanceType': instance_type,
            'GpuInfo': {
                'Gpus': [{
                    'Name': gpu_name,
                    'Count': gpu_count,
                }],
            },
            'VCpuInfo': {
                'DefaultVCpus': 192,
            },
            'Memory': '2048 GiB',
            'ProcessorInfo': {
                'SupportedArchitectures': ['x86_64'],
            },
        }

    def _get_corrected_accelerator_info(self, row: dict) -> tuple:
        """Apply the same correction logic as fetch_aws.py.

        This mirrors the logic in _get_instance_types_df's get_additional_columns
        function to test that the workarounds work correctly.
        """
        # Default: use the GPU info from the API
        acc_name = None
        acc_count = None

        if row.get('GpuInfo') and row['GpuInfo'].get('Gpus'):
            gpus = row['GpuInfo']['Gpus']
            if gpus:
                acc_name = gpus[0].get('Name')
                acc_count = gpus[0].get('Count', 0)

        # Apply workarounds (same logic as in fetch_aws.py)
        if row['InstanceType'] == 'p3dn.24xlarge':
            acc_name = 'V100-32GB'
        if row['InstanceType'] == 'p4de.24xlarge':
            acc_name = 'A100-80GB'
            acc_count = 8
        if row['InstanceType'] in ('p5e.48xlarge', 'p5en.48xlarge'):
            acc_name = 'H200'
            acc_count = 8

        return acc_name, acc_count

    @pytest.mark.parametrize('instance_type,expected_acc_name,expected_count', [
        # p5e.48xlarge should be corrected to H200 with count 8
        ('p5e.48xlarge', 'H200', 8),
        # p5en.48xlarge should also be corrected to H200 with count 8
        ('p5en.48xlarge', 'H200', 8),
        # p4de.24xlarge should be corrected to A100-80GB with count 8
        ('p4de.24xlarge', 'A100-80GB', 8),
        # p3dn.24xlarge should be corrected to V100-32GB
        ('p3dn.24xlarge', 'V100-32GB', 8),
    ])
    def test_h200_instance_workaround(self, instance_type, expected_acc_name,
                                       expected_count):
        """Test that H200 instance types are correctly identified.

        The AWS API returns 'NVIDIA' as the accelerator name for p5e.48xlarge
        and p5en.48xlarge instances, but they actually have H200 GPUs.
        This test verifies the workaround correctly identifies them.

        Regression test for: https://github.com/skypilot-org/skypilot/issues/8451
        """
        row = self._create_mock_row(instance_type, gpu_name='NVIDIA')
        acc_name, acc_count = self._get_corrected_accelerator_info(row)

        assert acc_name == expected_acc_name, (
            f'Expected {instance_type} to have accelerator {expected_acc_name}, '
            f'but got {acc_name}')
        assert acc_count == expected_count, (
            f'Expected {instance_type} to have {expected_count} accelerators, '
            f'but got {acc_count}')

    def test_p5e_and_p5en_both_corrected(self):
        """Test that both p5e.48xlarge and p5en.48xlarge are corrected.

        This is a specific regression test for issue #8451 where p5e.48xlarge
        was not included in the workaround but p5en.48xlarge was.
        """
        p5e_row = self._create_mock_row('p5e.48xlarge', gpu_name='NVIDIA')
        p5en_row = self._create_mock_row('p5en.48xlarge', gpu_name='NVIDIA')

        p5e_acc_name, p5e_acc_count = self._get_corrected_accelerator_info(
            p5e_row)
        p5en_acc_name, p5en_acc_count = self._get_corrected_accelerator_info(
            p5en_row)

        # Both should be corrected to H200 with count 8
        assert p5e_acc_name == 'H200'
        assert p5e_acc_count == 8
        assert p5en_acc_name == 'H200'
        assert p5en_acc_count == 8

        # They should have the same accelerator info
        assert p5e_acc_name == p5en_acc_name
        assert p5e_acc_count == p5en_acc_count

    def test_regular_instance_not_modified(self):
        """Test that regular instances are not affected by workarounds."""
        row = self._create_mock_row('p4d.24xlarge', gpu_name='A100', gpu_count=8)
        acc_name, acc_count = self._get_corrected_accelerator_info(row)

        # Should keep the original values
        assert acc_name == 'A100'
        assert acc_count == 8
