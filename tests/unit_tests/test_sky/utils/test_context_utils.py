"""Unit tests for sky.utils.context_utils module."""
from typing import Optional, Union

from sky.utils import context_utils


@context_utils.cancellation_guard
def original_function(arg1: int, arg2: str) -> Optional[Union[int, str]]:
    return None


def test_cancellation_guard_perserves_typecheck():
    # Verify that the decorated function has the same signature
    assert original_function.__name__ == 'original_function'
    assert original_function.__annotations__ == {
        'arg1': int,
        'arg2': str,
        'return': Optional[Union[int, str]]
    }

    # Verify that the decorated function can be called with the same signature
    assert original_function(1, 'test') is None
