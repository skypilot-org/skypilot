import pytest

from sky.api.requests import executor


def test_parallel_size_blocking():
    # Test with sufficient memory
    cpu_count = 4
    mem_size_gb = 10.5
    expected = 8
    assert executor._max_parallel_size_for_blocking(cpu_count,
                                                    mem_size_gb) == expected

    # Test with limited memory
    cpu_count = 4
    mem_size_gb = 0.7
    expected = 2
    assert executor._max_parallel_size_for_blocking(cpu_count,
                                                    mem_size_gb) == expected

    # Test invalid inputs
    with pytest.raises(AssertionError):
        executor._max_parallel_size_for_blocking(0, 1)
    with pytest.raises(AssertionError):
        executor._max_parallel_size_for_blocking(1, 0)


def test_parallel_size_non_blocking():
    # Test with sufficient memory
    cpu_count = 4
    mem_size_gb = 10.5
    expected = 85
    assert executor._max_parallel_size_for_non_blocking(cpu_count,
                                                        mem_size_gb) == expected

    # Test with insufficient memory
    cpu_count = 4
    mem_size_gb = 1
    expected = 2
    assert executor._max_parallel_size_for_non_blocking(cpu_count,
                                                        mem_size_gb) == expected
    # Test invalid inputs
    with pytest.raises(AssertionError):
        executor._max_parallel_size_for_non_blocking(0, 1)
    with pytest.raises(AssertionError):
        executor._max_parallel_size_for_non_blocking(1, 0)
