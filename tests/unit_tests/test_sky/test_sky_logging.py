"""Unit tests for sky.sky_logging module."""

import io
import logging
from unittest import mock

import pytest

from sky import sky_logging
from sky.utils import context
from sky.utils import env_options


@pytest.fixture
def stream():
    """Fixture to provide a string IO stream."""
    return io.StringIO()


@pytest.fixture
def handler(stream):
    """Fixture to provide an EnvAwareHandler instance."""
    return sky_logging.EnvAwareHandler(stream=stream, level=logging.INFO)


@pytest.fixture
def sensitive_handler(stream):
    """Fixture to provide a sensitive EnvAwareHandler instance."""
    return sky_logging.EnvAwareHandler(stream=stream,
                                       level=logging.INFO,
                                       sensitive=True)


@mock.patch('sky.utils.context.get', return_value=None)
def test_handler_default_level(mock_get, handler, monkeypatch):
    """Test handler's default log level behavior."""
    monkeypatch.setattr(env_options.Options.SHOW_DEBUG_INFO, 'get',
                        lambda: False)
    assert handler.level == logging.INFO
    handler.level = logging.DEBUG
    assert handler.level == logging.DEBUG


@mock.patch('sky.utils.context.get', return_value=context.Context())
def test_handler_with_debug_env(mock_get, handler, monkeypatch):
    """Test handler respects SHOW_DEBUG_INFO env var."""
    # Mock the env var to enable debug
    monkeypatch.setattr(env_options.Options.SHOW_DEBUG_INFO, 'get',
                        lambda: True)
    assert handler.level == logging.DEBUG

    # Mock the env var to disable debug
    monkeypatch.setattr(env_options.Options.SHOW_DEBUG_INFO, 'get',
                        lambda: False)
    assert handler.level == logging.INFO


@mock.patch('sky.utils.context.get', return_value=None)
def test_handler_without_context(mock_get, handler, monkeypatch):
    """Test handler behavior when no context is available."""
    # Even with debug env var set, should return original level
    monkeypatch.setattr(env_options.Options.SHOW_DEBUG_INFO, 'get',
                        lambda: True)
    assert handler.level == logging.INFO


@mock.patch('sky.utils.context.get', return_value=context.Context())
def test_sensitive_handler(mock_get, sensitive_handler, monkeypatch):
    """Test sensitive handler's log level behavior."""
    # Test when sensitive logs are suppressed
    monkeypatch.setattr(env_options.Options.SUPPRESS_SENSITIVE_LOG, 'get',
                        lambda: True)
    monkeypatch.setattr(env_options.Options.SHOW_DEBUG_INFO, 'get',
                        lambda: True)
    assert sensitive_handler.level == logging.INFO

    # Test when sensitive logs are not suppressed
    monkeypatch.setattr(env_options.Options.SUPPRESS_SENSITIVE_LOG, 'get',
                        lambda: False)
    assert sensitive_handler.level == logging.DEBUG


@mock.patch('sky.utils.context.get', return_value=context.Context())
def test_handler_invalid_level(mock_get, handler, monkeypatch):
    """Test setting invalid log levels."""
    monkeypatch.setattr(env_options.Options.SHOW_DEBUG_INFO, 'get',
                        lambda: False)
    with pytest.raises(ValueError):
        handler.level = 'INVALID_LEVEL'

    # Test valid level setting
    handler.level = logging.WARNING
    assert handler._level == logging.WARNING


@pytest.mark.asyncio
@mock.patch('sky.utils.context.get', return_value=None)
async def test_handler_with_context_override(mock_get, handler, monkeypatch):
    """Test setting invalid log levels."""
    assert handler.level == logging.INFO
    ctx = context.Context()
    ctx.override_envs({'SKYPILOT_DEBUG': '1'})
    mock_get.return_value = ctx
    assert handler.level == logging.DEBUG


@mock.patch('sky.utils.context.get', return_value=None)
def test_handler_output(mock_get, handler, stream, monkeypatch):
    """Sanity check."""
    monkeypatch.setattr(env_options.Options.SHOW_DEBUG_INFO, 'get',
                        lambda: False)
    logger = logging.getLogger('test')
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)

    logger.debug('Debug message')
    assert stream.getvalue() == ''

    logger.info('Info message')
    assert 'Info message' in stream.getvalue()

    handler.level = logging.DEBUG
    logger.debug('Debug message 2')
    assert 'Debug message 2' in stream.getvalue()
