"""Unit tests for sky.provision.instance_setup."""
import asyncio
import os
from unittest.mock import patch

import pytest

from sky.provision import instance_setup
from sky.usage import constants as usage_constants
from sky.usage import usage_lib
from sky.utils import context


@pytest.fixture
def _contextual_environ():
    """Replace os.environ with ContextualEnviron for the test."""
    original_environ = os.environ
    os.environ = context.ContextualEnviron(original_environ)
    yield
    os.environ = original_environ


class TestSetUsageRunIdCmd:
    """Tests that _set_usage_run_id_cmd picks up the per-context run id.

    In consolidation mode, the managed jobs controller process serves many
    jobs concurrently. Reading the run id from the process-wide
    usage_lib.messages.usage singleton would cause every worker cluster's
    skylet heartbeat to report the same run id. The fix is to prefer
    USAGE_RUN_ID_ENV_VAR via os.environ (hijacked to be SkyPilotContext
    aware), which is sourced from the client's run id forwarded through
    the controller's env file, so each request scopes its own run id.
    """

    @pytest.mark.asyncio
    async def test_uses_contextual_env_var_when_set(self, _contextual_environ):
        """Env var override takes priority over the process singleton."""
        # context.initialize creates a SkyPilotContext, which constructs an
        # asyncio.Event; on Python 3.9 that requires a running event loop,
        # so the test is async to keep pytest-asyncio's loop available.
        ctx = context.initialize()
        ctx.override_envs({usage_constants.USAGE_RUN_ID_ENV_VAR: 'ctx-run-id'})

        with patch.object(usage_lib.messages.usage, 'run_id',
                          'singleton-run-id'):
            cmd = instance_setup._set_usage_run_id_cmd()

        assert 'echo "ctx-run-id"' in cmd
        assert 'singleton-run-id' not in cmd

    @pytest.mark.asyncio
    async def test_falls_back_to_singleton_when_env_unset(
            self, _contextual_environ):
        """Without an env override, the process singleton is used."""
        context.initialize()  # context with no USAGE_RUN_ID_ENV_VAR override

        with patch.object(usage_lib.messages.usage, 'run_id',
                          'singleton-run-id'):
            cmd = instance_setup._set_usage_run_id_cmd()

        assert 'echo "singleton-run-id"' in cmd

    @pytest.mark.asyncio
    async def test_concurrent_contexts_get_isolated_run_ids(
            self, _contextual_environ):
        """Regression test for consolidation-mode run id sharing.

        Two concurrent coroutines, each in its own SkyPilotContext, should
        each see the run id they set, not each other's or the singleton's.
        """

        @context.contextual_async
        async def get_cmd_with_run_id(run_id: str, delay: float) -> str:
            ctx = context.get()
            assert ctx is not None
            ctx.override_envs({usage_constants.USAGE_RUN_ID_ENV_VAR: run_id})
            # Force interleaving so both coroutines have set their override
            # before either reads it back.
            await asyncio.sleep(delay)
            return instance_setup._set_usage_run_id_cmd()

        with patch.object(usage_lib.messages.usage, 'run_id',
                          'singleton-run-id'):
            cmd_a, cmd_b = await asyncio.gather(
                get_cmd_with_run_id('run-id-a', delay=0.05),
                get_cmd_with_run_id('run-id-b', delay=0.0),
            )

        assert 'echo "run-id-a"' in cmd_a, cmd_a
        assert 'echo "run-id-b"' in cmd_b, cmd_b
        assert 'singleton-run-id' not in cmd_a
        assert 'singleton-run-id' not in cmd_b
