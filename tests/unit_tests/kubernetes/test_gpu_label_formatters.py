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
        ('NVIDIA-L400', 'L400'),  # Should not match L4 or L40
        ('NVIDIA-L4', 'L4'),
        ('L40-GPU', 'L40'),
    ]
    for input_value, expected in test_cases:
        result = GFDLabelFormatter.get_accelerator_from_label_value(input_value)
        assert result == expected, f'Failed for {input_value}'
