"""Unit tests for sky.sky_logging module."""

import io
import logging

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
    return sky_logging.EnvAwareHandler(
        stream=stream,
        level=logging.INFO,
        sensitive=True
    )


def test_handler_default_level(handler):
    """Test handler's default log level behavior."""
    assert handler.level == logging.INFO
    handler.level = logging.DEBUG
    assert handler.level == logging.DEBUG


def test_handler_with_debug_env(handler, monkeypatch):
    """Test handler respects SHOW_DEBUG_INFO env var."""
    context.initialize()
    # Mock the env var to enable debug
    monkeypatch.setattr(env_options.Options.SHOW_DEBUG_INFO,
                       'get',
                       lambda: True)
    assert handler.level == logging.DEBUG

    # Mock the env var to disable debug
    monkeypatch.setattr(env_options.Options.SHOW_DEBUG_INFO,
                       'get',
                       lambda: False)
    assert handler.level == logging.INFO


def test_handler_without_context(handler, monkeypatch):
    """Test handler behavior when no context is available."""
    # Ensure no context is set
    context._CONTEXT.set(None)
    
    # Even with debug env var set, should return original level
    monkeypatch.setattr(env_options.Options.SHOW_DEBUG_INFO,
                       'get',
                       lambda: True)
    assert handler.level == logging.INFO


def test_sensitive_handler(sensitive_handler, monkeypatch):
    """Test sensitive handler's log level behavior."""
    context.initialize()
    
    # Test when sensitive logs are suppressed
    monkeypatch.setattr(env_options.Options.SUPPRESS_SENSITIVE_LOG,
                       'get',
                       lambda: True)
    monkeypatch.setattr(env_options.Options.SHOW_DEBUG_INFO,
                       'get',
                       lambda: True)
    assert sensitive_handler.level == logging.INFO

    # Test when sensitive logs are not suppressed
    monkeypatch.setattr(env_options.Options.SUPPRESS_SENSITIVE_LOG,
                       'get',
                       lambda: False)
    assert sensitive_handler.level == logging.DEBUG


def test_handler_level_setter(handler):
    """Test setting invalid log levels."""
    with pytest.raises(ValueError):
        handler.level = 'INVALID_LEVEL'

    # Test valid level setting
    handler.level = logging.WARNING
    assert handler._level == logging.WARNING


def test_handler_output(handler, stream):
    """Test handler actually logs messages at appropriate levels."""
    logger = logging.getLogger('test')
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)

    # Debug message should not appear at INFO level
    logger.debug('Debug message')
    assert stream.getvalue() == ''

    # Info message should appear
    logger.info('Info message')
    assert 'Info message' in stream.getvalue()

    # Set to debug level
    handler.level = logging.DEBUG
    logger.debug('Debug message 2')
    assert 'Debug message 2' in stream.getvalue()
