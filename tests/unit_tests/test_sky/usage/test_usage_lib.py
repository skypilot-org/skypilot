"""Unit tests for the per-context behavior of sky.usage.usage_lib.messages."""
import asyncio
import contextvars

import pytest

from sky.usage import usage_lib
from sky.utils import context


def _reset_module_state():
    """Reset per-context state between tests.

    Tests share the test runner's contextvars Context, so a value set by
    one test would otherwise leak into the next. Clearing
    ``_messages_var`` makes each test start with an empty Context.
    """
    usage_lib._messages_var.set(None)


def test_proxy_returns_same_instance_within_one_context():
    """Within one contextvars Context, lazy creation is idempotent."""
    _reset_module_state()

    a = usage_lib._get_messages()
    b = usage_lib._get_messages()
    assert a is b
    # Mutations are visible across reads of the same instance.
    a.usage.run_id = 'mutated'
    assert b.usage.run_id == 'mutated'


def test_proxy_attribute_access_round_trips():
    """messages.usage / messages.heartbeat go through the proxy."""
    _reset_module_state()

    # Read a few fields to make sure attribute proxying works.
    assert usage_lib.messages.usage.run_id is not None
    assert usage_lib.messages.heartbeat.interval_seconds > 0


@pytest.mark.asyncio
async def test_concurrent_contextual_async_get_isolated_messages():
    """Each @contextual_async coroutine has its own MessageCollection.

    Regression for the consolidation-mode bug: the consolidated managed
    jobs controller process serves many JobController coroutines, and a
    process-wide MessageCollection would let one job's mutations leak
    into another job's telemetry payload. With the proxy, each
    @contextual_async invocation runs inside its own copied
    contextvars Context and so triggers an independent lazy creation of
    its MessageCollection.
    """
    _reset_module_state()

    seen_ids = []

    @context.contextual_async
    async def write_and_read(tag: str, delay: float) -> str:
        # First access triggers lazy creation in this coroutine's
        # copied contextvars Context.
        usage_lib.messages.usage.run_id = f'run-{tag}'
        seen_ids.append(id(usage_lib._get_messages()))
        # Yield so the sibling coroutine has a chance to run between
        # our write and our read.
        await asyncio.sleep(delay)
        return usage_lib.messages.usage.run_id

    a, b = await asyncio.gather(
        write_and_read('a', delay=0.05),
        write_and_read('b', delay=0.0),
    )

    # Each coroutine sees its own write — no cross-contamination.
    assert a == 'run-a', a
    assert b == 'run-b', b
    # And the underlying MessageCollections are distinct objects.
    assert len(set(seen_ids)) == 2, seen_ids


@pytest.mark.asyncio
async def test_reset_isolates_to_current_context():
    """messages.reset(USAGE) only resets the current context's collection."""
    _reset_module_state()

    sibling_run_ids = []

    @context.contextual_async
    async def sibling_observe(delay: float) -> str:
        usage_lib.messages.usage.run_id = 'sibling-run'
        await asyncio.sleep(delay)
        return usage_lib.messages.usage.run_id

    @context.contextual_async
    async def reset_and_observe(delay: float) -> str:
        usage_lib.messages.usage.run_id = 'reset-run'
        await asyncio.sleep(delay)
        usage_lib.messages.reset(usage_lib.MessageType.USAGE)
        # After reset the current context has a fresh UsageMessageToReport;
        # its run_id should NOT be 'reset-run' anymore.
        return usage_lib.messages.usage.run_id

    sibling_id, post_reset_id = await asyncio.gather(
        sibling_observe(delay=0.05),
        reset_and_observe(delay=0.0),
    )

    # Sibling kept its run id across the other coroutine's reset.
    assert sibling_id == 'sibling-run'
    # The reset coroutine sees a fresh UsageMessageToReport, not its
    # earlier 'reset-run'.
    assert post_reset_id != 'reset-run'


def test_messages_proxy_supports_collection_protocol():
    """The proxy exposes __getitem__ / items / values like the original."""
    _reset_module_state()

    by_index = usage_lib.messages[usage_lib.MessageType.USAGE]
    by_attr = usage_lib.messages.usage
    assert by_index is by_attr

    keys = {k for k, _ in usage_lib.messages.items()}
    assert usage_lib.MessageType.USAGE in keys
    assert usage_lib.MessageType.HEARTBEAT in keys
    assert usage_lib.MessageType.SERVER_HEARTBEAT in keys

    values = list(usage_lib.messages.values())
    assert len(values) == 3


def test_install_fresh_messages_overrides_inherited_collection(monkeypatch):
    """install_fresh_messages_for_current_context replaces an inherited MC.

    Regression: in consolidation mode, class-body decorators like
    ``@usage_lib.messages.usage.update_runtime('provision')`` trigger
    lazy MC creation at module import time, against the controller
    process's startup env. Subsequent JobController coroutines
    inherit that MC via ContextVar copy and would otherwise share it.
    install_fresh_messages_for_current_context lets a per-task entry
    point install its own MC after establishing a per-task env.
    """
    _reset_module_state()

    # Simulate a "parent" creating an MC against env=startup-run.
    monkeypatch.setenv(usage_lib.constants.USAGE_RUN_ID_ENV_VAR, 'startup-run')
    inherited = usage_lib._get_messages()
    assert inherited.usage.run_id == 'startup-run'

    # Now flip the env (mimicking dotenv into ctx.override_envs from a
    # per-job env file) and install a fresh MC.
    monkeypatch.setenv(usage_lib.constants.USAGE_RUN_ID_ENV_VAR, 'per-task-run')
    usage_lib.install_fresh_messages_for_current_context()

    refreshed = usage_lib._get_messages()
    assert refreshed is not inherited
    assert refreshed.usage.run_id == 'per-task-run'


@pytest.mark.asyncio
async def test_install_fresh_messages_isolates_concurrent_tasks(monkeypatch):
    """When the parent Context already has an MC, two
    @contextual_async children that each call install_fresh_messages
    do not see each other's state — even though they would otherwise
    inherit the parent's MC."""
    _reset_module_state()

    # Create a parent MC so children would otherwise inherit it.
    monkeypatch.setenv(usage_lib.constants.USAGE_RUN_ID_ENV_VAR, 'parent-run')
    _ = usage_lib._get_messages()

    @context.contextual_async
    async def per_task(run_id: str, delay: float) -> str:
        # Set the env first (mimicking the env-file load), then install
        # a fresh MC, then read back through the proxy.
        monkeypatch.setenv(usage_lib.constants.USAGE_RUN_ID_ENV_VAR, run_id)
        usage_lib.install_fresh_messages_for_current_context()
        await asyncio.sleep(delay)
        return usage_lib.messages.usage.run_id

    a, b = await asyncio.gather(
        per_task('task-a', delay=0.05),
        per_task('task-b', delay=0.0),
    )

    assert a == 'task-a', a
    assert b == 'task-b', b
