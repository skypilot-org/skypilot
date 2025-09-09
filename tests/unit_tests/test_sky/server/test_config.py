from unittest import mock

import pytest

from sky.server import config


@mock.patch('sky.utils.common_utils.get_mem_size_gb', return_value=8)
@mock.patch('sky.utils.common_utils.get_cpu_count', return_value=4)
def test_compute_server_config_on_minimal_deployment(cpu_count, mem_size_gb):
    # Test deployment mode
    c = config.compute_server_config(deploy=True)
    assert c.num_server_workers == 3
    assert c.long_worker_config.garanteed_parallelism == 5
    assert c.long_worker_config.burstable_parallelism == 0
    assert c.short_worker_config.garanteed_parallelism == 8
    assert c.short_worker_config.burstable_parallelism == 0
    assert c.queue_backend == config.QueueBackend.MULTIPROCESSING


@mock.patch('sky.utils.common_utils.get_mem_size_gb', return_value=784)
@mock.patch('sky.utils.common_utils.get_cpu_count', return_value=196)
def test_compute_server_config_on_large_deployment(cpu_count, mem_size_gb):
    # Test local mode with low resources
    c = config.compute_server_config(deploy=True)
    assert c.num_server_workers == 196
    assert c.long_worker_config.garanteed_parallelism == 392
    assert c.long_worker_config.burstable_parallelism == 0
    assert c.short_worker_config.garanteed_parallelism == 1790
    assert c.short_worker_config.burstable_parallelism == 0
    assert c.queue_backend == config.QueueBackend.MULTIPROCESSING


@mock.patch('sky.utils.common_utils.get_mem_size_gb', return_value=16)
@mock.patch('sky.utils.common_utils.get_cpu_count', return_value=4)
def test_compute_server_config(cpu_count, mem_size_gb):
    # Test deployment mode
    c = config.compute_server_config(deploy=True)
    assert c.num_server_workers == 4
    assert c.long_worker_config.garanteed_parallelism == 8
    assert c.long_worker_config.burstable_parallelism == 0
    assert c.short_worker_config.garanteed_parallelism == 30
    assert c.short_worker_config.burstable_parallelism == 0
    assert c.queue_backend == config.QueueBackend.MULTIPROCESSING

    # Test local mode with normal resources
    c = config.compute_server_config(deploy=False)
    assert c.num_server_workers == 1
    assert c.long_worker_config.garanteed_parallelism == 4
    assert c.long_worker_config.burstable_parallelism == 1024
    assert c.short_worker_config.garanteed_parallelism == 35
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
    assert c.short_worker_config.garanteed_parallelism == 3
    assert c.short_worker_config.burstable_parallelism == 0
    assert c.queue_backend == config.QueueBackend.MULTIPROCESSING


def test_parallel_size_long():
    # Test with insufficient memory
    num_server_workers = 1
    cpu_count = 4
    mem_size_gb = 2
    expected = 1
    assert config._max_long_worker_parallism(cpu_count, mem_size_gb,
                                             num_server_workers) == expected

    # Test with sufficient memory
    num_server_workers = 1
    cpu_count = 4
    mem_size_gb = 12.5
    expected = 8
    assert config._max_long_worker_parallism(cpu_count, mem_size_gb,
                                             num_server_workers) == expected

    # Test with limited memory
    num_server_workers = 1
    cpu_count = 4
    mem_size_gb = 2.7
    expected = 1
    assert config._max_long_worker_parallism(cpu_count, mem_size_gb,
                                             num_server_workers) == expected


def test_parallel_size_short():
    # Test with insufficient memory
    num_server_workers = 1
    num_long_workers = 1
    mem_size_gb = 2
    expected = 3
    assert config._max_short_worker_parallism(mem_size_gb, num_server_workers,
                                              num_long_workers) == expected

    # Test with sufficient memory
    num_server_workers = 2
    num_long_workers = 8
    mem_size_gb = 13.7
    expected = 25
    assert config._max_short_worker_parallism(mem_size_gb, num_server_workers,
                                              num_long_workers) == expected

    # Test with limited memory
    num_server_workers = 1
    num_long_workers = 1
    mem_size_gb = 3
    expected = 3
    assert config._max_short_worker_parallism(mem_size_gb, num_server_workers,
                                              num_long_workers) == expected
