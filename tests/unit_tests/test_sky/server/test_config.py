from unittest import mock

import pytest

from sky.server import config


@mock.patch('sky.utils.common_utils.get_mem_size_gb', return_value=16)
@mock.patch('sky.utils.common_utils.get_cpu_count', return_value=4)
def test_compute_server_config(cpu_count, mem_size_gb):
    # Test deployment mode
    c = config.compute_server_config(deploy=True)
    assert c.num_server_workers == 4
    assert c.long_worker_config.garanteed_parallelism == 8
    assert c.long_worker_config.burstable_parallelism == 0
    assert c.short_worker_config.garanteed_parallelism == 43
    assert c.short_worker_config.burstable_parallelism == 0
    assert c.queue_backend == config.QueueBackend.MULTIPROCESSING

    # Test local mode with normal resources
    c = config.compute_server_config(deploy=False)
    assert c.num_server_workers == 1
    assert c.long_worker_config.garanteed_parallelism == 4
    assert c.long_worker_config.burstable_parallelism == 1024
    assert c.short_worker_config.garanteed_parallelism == 49
    assert c.short_worker_config.burstable_parallelism == 1024
    assert c.queue_backend == config.QueueBackend.LOCAL


@mock.patch('sky.utils.common_utils.get_mem_size_gb', return_value=1)
@mock.patch('sky.utils.common_utils.get_cpu_count', return_value=1)
def test_compute_server_config_low_resources(cpu_count, mem_size_gb):
    # Test local mode with low resources
    c = config.compute_server_config(deploy=False)
    assert c.num_server_workers == 1
    assert c.long_worker_config.garanteed_parallelism == 0
    assert c.long_worker_config.burstable_parallelism == 1024
    assert c.short_worker_config.garanteed_parallelism == 0
    assert c.short_worker_config.burstable_parallelism == 1024
    assert c.queue_backend == config.QueueBackend.LOCAL

    # Test deploymen mode with low resources
    c = config.compute_server_config(deploy=True)
    assert c.num_server_workers == 1
    assert c.long_worker_config.garanteed_parallelism == 1
    assert c.long_worker_config.burstable_parallelism == 0
    assert c.short_worker_config.garanteed_parallelism == 2
    assert c.short_worker_config.burstable_parallelism == 0
    assert c.queue_backend == config.QueueBackend.MULTIPROCESSING


def test_parallel_size_long():
    # Test with insufficient memory
    cpu_count = 4
    mem_size_gb = 2
    expected = 1
    assert config._max_long_worker_parallism(cpu_count, mem_size_gb) == expected

    # Test with sufficient memory
    cpu_count = 4
    mem_size_gb = 12.5
    expected = 8
    assert config._max_long_worker_parallism(cpu_count, mem_size_gb) == expected

    # Test with limited memory
    cpu_count = 4
    mem_size_gb = 2.7
    expected = 1
    assert config._max_long_worker_parallism(cpu_count, mem_size_gb) == expected


def test_parallel_size_short():
    # Test with insufficient memory
    blocking_size = 1
    mem_size_gb = 2
    expected = 2
    assert config._max_short_worker_parallism(mem_size_gb,
                                              blocking_size) == expected

    # Test with sufficient memory
    blocking_size = 8
    mem_size_gb = 12.5
    expected = 29
    assert config._max_short_worker_parallism(mem_size_gb,
                                              blocking_size) == expected

    # Test with limited memory
    blocking_size = 1
    mem_size_gb = 3
    expected = 2
    assert config._max_short_worker_parallism(mem_size_gb,
                                              blocking_size) == expected
