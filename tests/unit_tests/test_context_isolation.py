"""Tests for SkyPilotContext isolation with concurrent async tasks.

Regression test for a race condition where concurrent job group launches
corrupt shared env overrides. When multiple tasks pop/restore the same
env var on a shared SkyPilotContext, the second task's pop sees the var
already deleted and can't restore it, causing get_user_hash() to fall
back to the wrong hash file.

The fix (contextual_async on launch()) gives each concurrent launch its
own SkyPilotContext copy, isolating the pop/restore.
"""
import asyncio
import os

import pytest

from sky.utils import context


@pytest.fixture(autouse=True)
def _setup_contextual_environ():
    """Replace os.environ with ContextualEnviron for the test."""
    original_environ = os.environ
    os.environ = context.ContextualEnviron(original_environ)
    yield
    os.environ = original_environ


def _setup_parent_context(user_id: str) -> context.SkyPilotContext:
    """Initialize a SkyPilotContext with a USER_ID override."""
    ctx = context.initialize()
    ctx.override_envs({'USER_ID': user_id})
    return ctx


async def _pop_yield_restore_read(name: str, delay: float) -> str:
    """Simulate the ENV_VARS_TO_CLEAR pop/restore pattern from _launch().

    Pops USER_ID, yields (simulating api_start), restores it, then reads
    it back (simulating sdk.launch's get_user_hash()).
    """
    old_val = os.environ.pop('USER_ID', None)
    await asyncio.sleep(delay)
    if old_val is not None:
        os.environ['USER_ID'] = old_val
    # Read the value back (like get_user_hash does)
    return os.environ.get('USER_ID', 'FALLBACK_HASH')


@pytest.mark.asyncio
async def test_concurrent_pop_restore_race_without_isolation():
    """Demonstrate the race WITHOUT context isolation.

    Task B's pop returns None (already deleted by A), so it can't
    restore the var. If B reads before A restores, it gets the fallback.
    """
    _setup_parent_context('correct_hash')

    # Force interleaving: A pops first but restores late, B pops second
    # (gets None) and reads before A restores.
    results = await asyncio.gather(
        _pop_yield_restore_read('A', delay=0.05),  # slow api_start
        _pop_yield_restore_read('B', delay=0.0),  # fast api_start
    )

    # B gets fallback because it popped None and couldn't restore,
    # and it read before A had a chance to restore.
    assert results[1] == 'FALLBACK_HASH', (
        'Expected race condition: task B should get fallback hash')


@pytest.mark.asyncio
async def test_concurrent_pop_restore_with_contextual_async():
    """Verify that @contextual_async isolates each task's env overrides.

    This is the fix: each launch() gets its own SkyPilotContext copy,
    so pop/restore in one task doesn't affect another.
    """
    _setup_parent_context('correct_hash')

    @context.contextual_async
    async def isolated_pop_restore(name: str, delay: float) -> str:
        return await _pop_yield_restore_read(name, delay)

    results = await asyncio.gather(
        isolated_pop_restore('A', delay=0.05),
        isolated_pop_restore('B', delay=0.0),
    )

    # Both tasks should see the correct hash
    assert results[0] == 'correct_hash', (
        f'Task A got wrong hash: {results[0]}')
    assert results[1] == 'correct_hash', (
        f'Task B got wrong hash: {results[1]}')
