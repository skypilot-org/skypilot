import gc
import tracemalloc

import pytest

from sky import check as sky_check


# if run with other tests, the memory usage may be
# affected by other tests, so we run it separately.
@pytest.mark.serial
def test_sky_check_memory_usage():
    tracemalloc.start()
    initial_memory = tracemalloc.get_traced_memory()[0]
    print(f'Initial memory: {initial_memory}')
    sky_check.check(quiet=True, verbose=True)
    gc.collect()
    final_memory = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()
    print(f'Final memory: {final_memory}')
    memory_diff_mb = (final_memory - initial_memory) / 1024 / 1024
    # (syang) As of 09/05 the number I get on my laptop is
    # just below 40 (39.95 - 39.98), so if it goes over 45
    # we may need to investigate potential memory increase.
    assert memory_diff_mb < 45, (
        f'Memory usage increased by {memory_diff_mb} MB')
