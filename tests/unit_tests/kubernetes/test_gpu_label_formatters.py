"""Tests for GPU label formatting in Kubernetes integration.

Tests verify correct GPU detection from Kubernetes labels.
"""
import pytest

from sky.provision.kubernetes.utils import GFDLabelFormatter


def test_gfd_label_formatter():
    """Test word boundary regex matching in GFDLabelFormatter."""
    # Test various GPU name patterns
    test_cases = [
        ('NVIDIA-L4-24GB', 'L4'),
        ('NVIDIA-L40-48GB', 'L40'),
        ('NVIDIA-L40S-48GB', 'L40S'),  # L40S should not match L40 or L4
        ('NVIDIA-L40S', 'L40S'),
        ('NVIDIA-L400', 'L400'),  # Should not match L4, L40, or L40S
        ('NVIDIA-L4', 'L4'),
        ('L40-GPU', 'L40'),
        ('L40S-GPU', 'L40S'),
    ]
    for input_value, expected in test_cases:
        result = GFDLabelFormatter.get_accelerator_from_label_value(input_value)
        assert result == expected, f'Failed for {input_value}'
