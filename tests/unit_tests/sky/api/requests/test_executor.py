import pytest

from sky.server.requests import executor


def test_parallel_size_blocking():
    # Test with insufficient memory
    cpu_count = 4
    mem_size_gb = 0
    expected = 1
    assert executor._max_parallel_size_for_blocking(cpu_count,
                                                    mem_size_gb) == expected

    # Test with sufficient memory
    cpu_count = 4
    mem_size_gb = 10.5
    expected = 8
    assert executor._max_parallel_size_for_blocking(cpu_count,
                                                    mem_size_gb) == expected

    # Test with limited memory
    cpu_count = 4
    mem_size_gb = 0.7
    expected = 1
    assert executor._max_parallel_size_for_blocking(cpu_count,
                                                    mem_size_gb) == expected


def test_parallel_size_non_blocking():
    # Test with insufficient memory
    blocking_size = 1
    mem_size_gb = 0
    expected = 1
    assert executor._max_parallel_size_for_non_blocking(
        mem_size_gb, blocking_size) == expected

    # Test with sufficient memory
    blocking_size = 8
    mem_size_gb = 10.5
    expected = 56
    assert executor._max_parallel_size_for_non_blocking(
        mem_size_gb, blocking_size) == expected

    # Test with limited memory
    blocking_size = 1
    mem_size_gb = 1
    expected = 5
    assert executor._max_parallel_size_for_non_blocking(
        mem_size_gb, blocking_size) == expected
