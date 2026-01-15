"""Tests for GPU label formatting in Kubernetes integration.

Tests verify correct GPU detection from Kubernetes labels.
"""

from sky.provision.kubernetes import constants as kubernetes_constants
from sky.provision.kubernetes.utils import GFDLabelFormatter


class TestCanonicalGPUNames:
    """Tests for the shared CANONICAL_GPU_NAMES constant."""

    def test_canonical_gpu_names_order(self):
        """Test that longer GPU names come before shorter substrings.

        This is critical for correct substring matching in the GPU labeler.
        For example, 'L40S' must come before 'L40' which must come before 'L4'.
        """
        names = kubernetes_constants.CANONICAL_GPU_NAMES

        # L40S must come before L40 and L4
        assert names.index('L40S') < names.index('L40')
        assert names.index('L40') < names.index('L4')

        # H100-80GB must come before H100
        assert names.index('H100-80GB') < names.index('H100')

        # A100-80GB must come before A100
        assert names.index('A100-80GB') < names.index('A100')

        # A10G must come before A10
        assert names.index('A10G') < names.index('A10')

    def test_canonical_gpu_names_contains_latest_gpus(self):
        """Test that all latest generation GPUs are included."""
        names = kubernetes_constants.CANONICAL_GPU_NAMES

        # Blackwell architecture
        assert 'B200' in names
        assert 'GB200' in names

        # Hopper architecture
        assert 'H100' in names
        assert 'H100-80GB' in names
        assert 'H200' in names
        assert 'GH200' in names

        # Ada Lovelace architecture
        assert 'L4' in names
        assert 'L40' in names
        assert 'L40S' in names

        # Ampere architecture
        assert 'A100' in names
        assert 'A100-80GB' in names
        assert 'A10' in names
        assert 'A10G' in names


class TestGFDLabelFormatter:
    """Tests for GFDLabelFormatter GPU detection."""

    def test_l4_l40_l40s_detection(self):
        """Test correct detection of L4, L40, and L40S GPUs.

        This was the original bug: L40S was being misidentified as L4.
        """
        test_cases = [
            ('NVIDIA-L4-24GB', 'L4'),
            ('NVIDIA-L40-48GB', 'L40'),
            ('NVIDIA-L40S-48GB', 'L40S'),
            ('NVIDIA-L40S', 'L40S'),
            ('NVIDIA-L4', 'L4'),
            ('L40-GPU', 'L40'),
            ('L40S-GPU', 'L40S'),
        ]
        for input_value, expected in test_cases:
            result = GFDLabelFormatter.get_accelerator_from_label_value(
                input_value)
            assert result == expected, f'Failed for {input_value}'

    def test_l400_not_matched_as_l4_or_l40(self):
        """Test that L400 falls back correctly and doesn't match L4/L40."""
        result = GFDLabelFormatter.get_accelerator_from_label_value(
            'NVIDIA-L400')
        # L400 is not in canonical names, so it should use fallback
        assert result == 'L400', f'Expected L400, got {result}'

    def test_h100_variants(self):
        """Test H100 variant detection including 80GB models."""
        test_cases = [
            ('NVIDIA-H100-SXM-80GB', 'H100-80GB'),
            ('NVIDIA-H100-PCIE-80GB', 'H100-80GB'),
            ('NVIDIA-H100-80GB-HBM3', 'H100-80GB'),
            ('NVIDIA-H100-SXM', 'H100'),
            ('NVIDIA-H100', 'H100'),
        ]
        for input_value, expected in test_cases:
            result = GFDLabelFormatter.get_accelerator_from_label_value(
                input_value)
            assert result == expected, f'Failed for {input_value}'

    def test_a100_variants(self):
        """Test A100 variant detection including 80GB models."""
        test_cases = [
            ('NVIDIA-A100-SXM4-80GB', 'A100-80GB'),
            ('NVIDIA-A100-PCIE-80GB', 'A100-80GB'),
            ('NVIDIA-A100-80GB', 'A100-80GB'),
            ('NVIDIA-A100-SXM4-40GB', 'A100'),
            ('NVIDIA-A100-40GB', 'A100'),
            ('NVIDIA-A100', 'A100'),
        ]
        for input_value, expected in test_cases:
            result = GFDLabelFormatter.get_accelerator_from_label_value(
                input_value)
            assert result == expected, f'Failed for {input_value}'

    def test_blackwell_gpus(self):
        """Test Blackwell architecture GPU detection."""
        test_cases = [
            ('NVIDIA-B200', 'B200'),
            ('NVIDIA-B100', 'B100'),
            ('NVIDIA-GB200', 'GB200'),
            ('NVIDIA-GB300', 'GB300'),
        ]
        for input_value, expected in test_cases:
            result = GFDLabelFormatter.get_accelerator_from_label_value(
                input_value)
            assert result == expected, f'Failed for {input_value}'

    def test_hopper_gpus(self):
        """Test Hopper architecture GPU detection."""
        test_cases = [
            ('NVIDIA-H200', 'H200'),
            ('NVIDIA-GH200', 'GH200'),
        ]
        for input_value, expected in test_cases:
            result = GFDLabelFormatter.get_accelerator_from_label_value(
                input_value)
            assert result == expected, f'Failed for {input_value}'

    def test_ampere_gpus(self):
        """Test Ampere architecture GPU detection."""
        test_cases = [
            ('NVIDIA-A10G', 'A10G'),
            ('NVIDIA-A10', 'A10'),
            ('NVIDIA-A30', 'A30'),
            ('NVIDIA-A40', 'A40'),
        ]
        for input_value, expected in test_cases:
            result = GFDLabelFormatter.get_accelerator_from_label_value(
                input_value)
            assert result == expected, f'Failed for {input_value}'

    def test_older_gpus(self):
        """Test older GPU architecture detection."""
        test_cases = [
            ('NVIDIA-V100-SXM2-32GB', 'V100'),
            ('NVIDIA-V100', 'V100'),
            ('NVIDIA-T4', 'T4'),
            ('NVIDIA-P100', 'P100'),
            ('NVIDIA-K80', 'K80'),
        ]
        for input_value, expected in test_cases:
            result = GFDLabelFormatter.get_accelerator_from_label_value(
                input_value)
            assert result == expected, f'Failed for {input_value}'

    def test_fallback_for_unknown_gpus(self):
        """Test fallback behavior for GPUs not in canonical list."""
        test_cases = [
            ('NVIDIA-RTX-A6000', 'RTX-A6000'),
            ('NVIDIA-GEFORCE-RTX-3090', 'RTX-3090'),
            ('NVIDIA-RTX-6000', 'RTX6000'),
        ]
        for input_value, expected in test_cases:
            result = GFDLabelFormatter.get_accelerator_from_label_value(
                input_value)
            assert result == expected, f'Failed for {input_value}'


class TestGPULabelerMatching:
    """Tests for the GPU labeler substring matching logic.

    The GPU labeler uses substring matching (canonical_name.lower() in gpu_name)
    which requires careful ordering of canonical names.
    """

    def _simulate_labeler_match(self, gpu_name: str) -> str:
        """Simulate the GPU labeler matching logic."""
        for canonical_name in kubernetes_constants.CANONICAL_GPU_NAMES:
            if canonical_name.lower() in gpu_name.lower():
                return canonical_name.lower()
        # Fallback logic from the labeler
        return gpu_name.lower().replace('nvidia ',
                                        '').replace('geforce ', '').replace(
                                            'rtx ', 'rtx').replace(' ', '-')

    def test_l40s_not_matched_as_l4(self):
        """Test that L40S is not incorrectly matched as L4.

        This was the original bug reported by the customer.
        """
        result = self._simulate_labeler_match('NVIDIA L40S')
        assert result == 'l40s', f'Expected l40s, got {result}'

    def test_l40_not_matched_as_l4(self):
        """Test that L40 is not incorrectly matched as L4."""
        result = self._simulate_labeler_match('NVIDIA L40')
        assert result == 'l40', f'Expected l40, got {result}'

    def test_l4_matched_correctly(self):
        """Test that L4 is still matched correctly."""
        result = self._simulate_labeler_match('NVIDIA L4')
        assert result == 'l4', f'Expected l4, got {result}'

    def test_nvidia_smi_output_formats(self):
        """Test various nvidia-smi output formats."""
        test_cases = [
            ('NVIDIA L40S', 'l40s'),
            ('NVIDIA L40S 48GB', 'l40s'),
            ('NVIDIA H100 80GB HBM3', 'h100-80gb'),
            ('NVIDIA H100 PCIe', 'h100'),
            ('NVIDIA A100-SXM4-80GB', 'a100-80gb'),
            ('NVIDIA A100-SXM4-40GB', 'a100'),
            ('NVIDIA GeForce RTX 3090', 'rtx3090'),
        ]
        for gpu_name, expected in test_cases:
            result = self._simulate_labeler_match(gpu_name)
            assert result == expected, f'Failed for {gpu_name}: got {result}'


# Keep the original test for backwards compatibility
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
