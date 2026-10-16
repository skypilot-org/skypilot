"""Tests for the shared GPU image-selection helpers."""

import pytest

from sky.clouds.utils import gpu_utils


@pytest.mark.parametrize('acc_name', ['V100', 'P100', 'P4', 'M60'])
def test_pre_turing_gpus_are_legacy(acc_name):
    assert gpu_utils.is_legacy_driver_gpu(acc_name)
    assert acc_name in gpu_utils.LEGACY_DRIVER_GPUS


@pytest.mark.parametrize('acc_name',
                         ['T4', 'A100', 'A100-80GB', 'L4', 'H100', 'B200'])
def test_turing_and_later_gpus_are_not_legacy(acc_name):
    assert not gpu_utils.is_legacy_driver_gpu(acc_name)


def test_k80_is_not_in_legacy_set():
    # K80 has its own dedicated image and is handled separately by each cloud,
    # so it must not be routed through the legacy proprietary-driver image.
    assert not gpu_utils.is_legacy_driver_gpu('K80')
    assert 'K80' not in gpu_utils.LEGACY_DRIVER_GPUS


def test_unknown_gpu_is_not_legacy():
    assert not gpu_utils.is_legacy_driver_gpu('NotAGPU')
