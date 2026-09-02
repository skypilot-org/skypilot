"""Tests that RunPod GPU names stay in sync across catalog and provisioner."""
import pytest

# The runpod SDK is only installed with the `runpod` extra.
pytest.importorskip('runpod')

# pylint: disable=wrong-import-position
from sky.catalog.data_fetchers import fetch_runpod
from sky.provision.runpod import utils


def test_catalog_gpu_names_are_provisionable():
    """Every GPU name the catalog can emit must map to a RunPod GPU ID.

    The catalog names GPUs with `fetch_runpod.format_gpu_name`, while
    provisioning translates that name back to a RunPod GPU ID through
    `GPU_NAME_MAP`. A name missing from the map is selectable via
    `sky show-gpus` but fails to launch.
    """
    missing = sorted(
        set(fetch_runpod.DEFAULT_GPU_INFO) - set(utils.GPU_NAME_MAP))
    assert not missing, (
        f'GPUs missing from GPU_NAME_MAP: {missing}. Add them to '
        'sky/provision/runpod/utils.py, using the GPU ID reported by '
        'runpod.get_gpus().')
