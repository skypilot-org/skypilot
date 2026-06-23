"""Test the GCP class."""

import pytest

from sky.clouds import gcp as gcp_mod


class TestGetGpuImageId:
    """Tests for GCP._get_gpu_image_id GPU image selection."""

    @pytest.mark.parametrize('acc_name',
                             ['T4', 'A100', 'A100-80GB', 'L4', 'H100', 'B200'])
    def test_turing_and_later_uses_cuda13_default(self, acc_name):
        assert gcp_mod.GCP._get_gpu_image_id(
            acc_name) == gcp_mod._DEFAULT_GPU_IMAGE_ID

    @pytest.mark.parametrize('acc_name', ['V100', 'P100', 'P4', 'M60'])
    def test_pre_turing_uses_legacy_cuda12(self, acc_name):
        assert gcp_mod.GCP._get_gpu_image_id(
            acc_name) == gcp_mod._DEFAULT_GPU_CUDA12_IMAGE_ID

    def test_k80_uses_k80_image(self):
        assert gcp_mod.GCP._get_gpu_image_id(
            'K80') == gcp_mod._DEFAULT_GPU_K80_IMAGE_ID
