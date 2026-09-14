"""Semantics of the request-scoped execution-pause opt-out."""

import pytest

from sky.utils import execution_pause


def test_pause_allowed_by_default():
    assert execution_pause.pause_allowed() is True


def test_disallowed_inside_the_block_and_restored_after():
    with execution_pause.disallow_pause():
        assert execution_pause.pause_allowed() is False
    assert execution_pause.pause_allowed() is True


def test_restored_when_the_block_raises():
    """A wrapper whose launch failed must not leave pausing disabled for
    whatever the worker process runs next."""
    with pytest.raises(RuntimeError):
        with execution_pause.disallow_pause():
            raise RuntimeError('boom')
    assert execution_pause.pause_allowed() is True


def test_nesting_restores_the_outer_state_not_the_default():
    with execution_pause.disallow_pause():
        with execution_pause.disallow_pause():
            assert execution_pause.pause_allowed() is False
        # The inner exit must not re-allow while the outer block is active.
        assert execution_pause.pause_allowed() is False
    assert execution_pause.pause_allowed() is True
