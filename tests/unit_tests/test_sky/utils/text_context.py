"""Unit tests for sky.utils.context module."""

import asyncio
import os
import pathlib
import tempfile

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
    # Test without override
    assert ctx.getenv('TEST_VAR', 'default') == 'default'

    # Test with override
    ctx.override_envs({'TEST_VAR': 'overridden'})
    assert ctx.getenv('TEST_VAR', 'default') == 'overridden'

    # Test with existing env var
    os.environ['TEST_VAR2'] = 'original'
    assert ctx.getenv('TEST_VAR2') == 'original'
    ctx.override_envs({'TEST_VAR2': 'overridden2'})
    assert ctx.getenv('TEST_VAR2') == 'overridden2'


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
