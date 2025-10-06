"""Unit tests for sky.utils.context module."""

import asyncio
import os
import pathlib
import tempfile
from unittest import mock

import pytest

from sky.utils import context


@pytest.fixture
def ctx():
    """Fixture to provide a fresh context for each test."""
    context.initialize()
    yield context.get()
    # Cleanup
    if context.get()._log_file_handle is not None:
        context.get()._log_file_handle.close()


def test_context_initialization():
    """Test context initialization."""
    assert context.get() is None
    context.initialize()
    assert isinstance(context.get(), context.Context)


def test_context_cancellation(ctx):
    """Test context cancellation."""
    assert not ctx.is_canceled()
    ctx.cancel()
    assert ctx.is_canceled()


def test_log_redirection(ctx):
    """Test log file redirection."""
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
        log_path = pathlib.Path(f.name)

    try:
        # Test setting log file
        old_log = ctx.redirect_log(log_path)
        assert old_log is None
        assert ctx._log_file == log_path
        assert ctx._log_file_handle is not None

        # Test removing log file
        old_log = ctx.redirect_log(None)
        assert old_log == log_path
        assert ctx._log_file is None
        assert ctx._log_file_handle is None
    finally:
        # Cleanup
        if log_path.exists():
            log_path.unlink()


def test_env_overrides(ctx):
    """Test environment variable overrides."""
    # Setup env overrides
    ctx.override_envs({'TEST_VAR': 'overridden'})
    assert 'TEST_VAR' in ctx.env_overrides
    assert ctx.env_overrides['TEST_VAR'] == 'overridden'

    # Test adding more overrides
    ctx.override_envs({'TEST_VAR2': 'overridden2'})
    assert 'TEST_VAR2' in ctx.env_overrides
    assert ctx.env_overrides['TEST_VAR2'] == 'overridden2'
    # Original override should still be present
    assert ctx.env_overrides['TEST_VAR'] == 'overridden'


def test_contextual_environ_getitem(ctx):
    """Test ContextualEnviron __getitem__ functionality."""
    # Create a ContextualEnviron with a simple dict as base environ
    base_environ = {'BASE_VAR': 'base_value'}
    env = context.ContextualEnviron(base_environ)

    # Test getting a value from base environ
    assert env['BASE_VAR'] == 'base_value'

    # Test getting a value that doesn't exist should raise KeyError
    with pytest.raises(KeyError):
        _ = env['NON_EXISTENT']

    # Test override with context
    ctx.override_envs({'BASE_VAR': 'overridden_value'})
    assert env['BASE_VAR'] == 'overridden_value'

    # Test adding a new override
    ctx.override_envs({'NEW_VAR': 'new_value'})
    assert env['NEW_VAR'] == 'new_value'


def test_contextual_environ_iter(ctx):
    """Test ContextualEnviron __iter__ functionality."""
    # Create a ContextualEnviron with a simple dict as base environ
    base_environ = {'BASE_VAR1': 'value1', 'BASE_VAR2': 'value2'}
    env = context.ContextualEnviron(base_environ)

    # Without overrides, should iterate base environ
    keys = set(env)
    assert keys == {'BASE_VAR1', 'BASE_VAR2'}

    # With overrides, should include both base and overridden vars
    ctx.override_envs({'NEW_VAR': 'new_value', 'BASE_VAR1': 'override'})
    keys = set(env)
    assert keys == {'BASE_VAR1', 'BASE_VAR2', 'NEW_VAR'}


def test_contextual_environ_set_del(ctx):
    """Test ContextualEnviron set and delete operations."""
    # Create a ContextualEnviron with a simple dict as base environ
    base_environ = {'BASE_VAR': 'base_value'}
    env = context.ContextualEnviron(base_environ)

    # Set a new value in the base environ
    env['NEW_VAR'] = 'direct_value'
    assert 'NEW_VAR' not in base_environ, 'set env key in context should not affect the process env'

    # Delete a value from base environ
    del env['BASE_VAR']
    assert 'BASE_VAR' in base_environ, 'delete env key in context should not affect the process env'

    # Context overrides should take precedence over base
    ctx.override_envs({'NEW_VAR': 'override_value'})
    assert env['NEW_VAR'] == 'override_value'
    assert base_environ['BASE_VAR'] == 'base_value'
    assert env.get('BASE_VAR') is None


def test_contextual_environ_methods(ctx):
    """Test other ContextualEnviron methods."""
    # Create a ContextualEnviron with a simple dict as base environ
    base_environ = {'BASE_VAR': 'base_value'}
    env = context.ContextualEnviron(base_environ)
    ctx.override_envs({'BASE_VAR': 'overridden_value'})

    copied = env.copy()
    assert copied == {'BASE_VAR': 'overridden_value'}
    assert copied is not base_environ

    # Test setdefault
    assert env.setdefault('BASE_VAR', 'default') == 'base_value'
    assert env.setdefault('NEW_DEFAULT', 'default_value') == 'default_value'
    assert base_environ['NEW_DEFAULT'] == 'default_value'


def test_contextual_environ_subprocess(ctx):
    """Test ContextualEnviron behavior with subprocesses."""
    import subprocess
    import sys

    # Save original os.environ to restore it later
    original_environ = os.environ

    try:
        # Create a ContextualEnviron and replace os.environ
        base_environ = dict(os.environ)
        contextual_env = context.ContextualEnviron(base_environ)
        os.environ = contextual_env

        # Set an environment variable override in the context
        test_var_name = 'SKYPILOT_TEST_SUBPROCESS_VAR'
        ctx.override_envs({test_var_name: 'initial_value'})

        assert os.getenv(test_var_name) == 'initial_value'

        print_envs = (f'import os; '
                      f'import time; '
                      f'print(os.getenv(\'{test_var_name}\', \'NOT_FOUND\')); '
                      'time.sleep(1); '
                      f'print(os.getenv(\'{test_var_name}\', \'NOT_FOUND\'))')

        # Run subprocess and capture its output
        proc = context.Popen([sys.executable, '-c', print_envs],
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE,
                             text=True)
        ctx.override_envs({test_var_name: 'updated_value'})
        proc.wait()

        output = proc.stdout.readlines()
        assert len(output) == 2
        # The subprocess should see the context-overridden env vars at spawn time
        assert output[0].strip() == 'initial_value'
        # The subprocess should not see the updated value after spawn
        assert output[1].strip() == 'initial_value'

        # Verify the environment variable is updated in current process
        assert os.getenv(test_var_name) == 'updated_value'
    finally:
        # Restore original os.environ
        os.environ = original_environ


def test_output_stream(ctx):
    """Test output stream selection."""
    import sys

    # Default should return fallback
    assert ctx.output_stream(sys.stdout) == sys.stdout

    # With log file should return log file handle
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
        log_path = pathlib.Path(f.name)
    try:
        ctx.redirect_log(log_path)
        assert ctx.output_stream(sys.stdout) == ctx._log_file_handle
    finally:
        if log_path.exists():
            log_path.unlink()


def test_contextual_streams(ctx):
    """Test contextual stdout/stderr."""
    import sys
    stdout = context.Stdout()
    stderr = context.Stderr()

    # Without log file should use original streams
    assert stdout._active_stream() == sys.stdout
    assert stderr._active_stream() == sys.stderr

    # With log file should use log file handle
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
        log_path = pathlib.Path(f.name)
    try:
        ctx.redirect_log(log_path)
        assert stdout._active_stream() == ctx._log_file_handle
        assert stderr._active_stream() == ctx._log_file_handle
    finally:
        if log_path.exists():
            log_path.unlink()


@pytest.mark.asyncio
async def test_async_context_isolation():
    """Test context isolation between coroutines."""
    context.initialize()
    ctx1 = context.get()

    async def other_coro():
        context.initialize()
        return context.get()

    ctx2 = await other_coro()
    assert ctx1 is not ctx2


@pytest.mark.asyncio
async def test_async_cancellation():
    """Test context cancellation in async environment."""
    context.initialize()
    ctx = context.get()

    cancel_called = False

    async def check_cancel():
        nonlocal cancel_called
        while not ctx.is_canceled():
            await asyncio.sleep(0.1)
        cancel_called = True

    task = asyncio.create_task(check_cancel())
    await asyncio.sleep(0.2)
    ctx.cancel()
    await asyncio.wait_for(task, timeout=1.0)
    assert cancel_called


def test_contextual_decorator():
    """Test that @contextual copies and decouples the context."""
    # Prepare an original context with an override
    context.initialize()
    parent = context.get()
    parent.override_envs({'K': 'v1'})

    @context.contextual
    def inner():
        child = context.get()
        # Child context should be a different instance
        assert child is not parent
        # Child should inherit parent's overrides at creation time
        assert child.env_overrides.get('K') == 'v1'
        # Mutating child should not affect parent
        child.override_envs({'K': 'v2', 'NEW': 'n'})
        assert parent.env_overrides.get('K') == 'v1'
        assert 'NEW' not in parent.env_overrides

    inner()
    # After returning, the active context should be restored to parent
    assert context.get() is parent
    # Parent remains unchanged by child's mutations
    assert parent.env_overrides.get('K') == 'v1'
    assert 'NEW' not in parent.env_overrides
