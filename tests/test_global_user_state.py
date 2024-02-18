import sys

import pytest

import sky


@pytest.mark.skipif(sys.platform != 'linux', reason='Only test in CI.')
def test_enabled_clouds_empty():
    # In test environment, no cloud should be enabled.
    assert sky.global_user_state.get_cached_enabled_clouds() == []
