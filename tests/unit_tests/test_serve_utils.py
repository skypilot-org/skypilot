import pytest

from sky import clouds
from sky.resources import Resources
from sky.serve import serve_utils


def test_task_fits():
    # Test exact fit.
    task_resources = Resources(cpus=1, memory=1, cloud=clouds.AWS())
    free_resources = Resources(cpus=1, memory=1, cloud=clouds.AWS())
    assert serve_utils._task_fits(task_resources, free_resources) is True

    # Test less CPUs than free.
    task_resources = Resources(cpus=1, memory=1, cloud=clouds.AWS())
    free_resources = Resources(cpus=2, memory=1, cloud=clouds.AWS())
    assert serve_utils._task_fits(task_resources, free_resources) is True

    # Test more CPUs than free.
    task_resources = Resources(cpus=2, memory=1, cloud=clouds.AWS())
    free_resources = Resources(cpus=1, memory=1, cloud=clouds.AWS())
    assert serve_utils._task_fits(task_resources, free_resources) is False

    # Test less  memory than free.
    task_resources = Resources(cpus=1, memory=1, cloud=clouds.AWS())
    free_resources = Resources(cpus=1, memory=2, cloud=clouds.AWS())
    assert serve_utils._task_fits(task_resources, free_resources) is True

    # Test more memory than free.
    task_resources = Resources(cpus=1, memory=2, cloud=clouds.AWS())
    free_resources = Resources(cpus=1, memory=1, cloud=clouds.AWS())
    assert serve_utils._task_fits(task_resources, free_resources) is False

    # Test GPU exact fit.
    task_resources = Resources(accelerators='A10:1', cloud=clouds.AWS())
    free_resources = Resources(accelerators='A10:1', cloud=clouds.AWS())
    assert serve_utils._task_fits(task_resources, free_resources) is True

    # Test GPUs less than free.
    task_resources = Resources(accelerators='A10:1', cloud=clouds.AWS())
    free_resources = Resources(accelerators='A10:2', cloud=clouds.AWS())
    assert serve_utils._task_fits(task_resources, free_resources) is True

    # Test GPUs more than free.
    task_resources = Resources(accelerators='A10:2', cloud=clouds.AWS())
    free_resources = Resources(accelerators='A10:1', cloud=clouds.AWS())
    assert serve_utils._task_fits(task_resources, free_resources) is False

    # Test resources exhausted.
    task_resources = Resources(cpus=1, memory=1, cloud=clouds.AWS())
    free_resources = Resources(cpus=None, memory=None, cloud=clouds.AWS())
    assert serve_utils._task_fits(task_resources, free_resources) is False
