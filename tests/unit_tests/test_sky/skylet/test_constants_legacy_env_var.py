"""Unit tests for getenv_server_with_legacy in sky.skylet.constants."""
import logging
from unittest import mock

from sky.skylet import constants

_NEW = 'SKYPILOT_SERVER_TEST_VAR'
_LEGACY = 'SKYPILOT_TEST_VAR'


def test_returns_new_when_only_new_set(monkeypatch):
    monkeypatch.setenv(_NEW, 'new-value')
    monkeypatch.delenv(_LEGACY, raising=False)
    assert constants.getenv_server_with_legacy(_NEW, _LEGACY) == 'new-value'


def test_returns_legacy_when_only_legacy_set(monkeypatch):
    """Legacy-only path must emit a debug-level deprecation hint."""
    monkeypatch.delenv(_NEW, raising=False)
    monkeypatch.setenv(_LEGACY, 'legacy-value')
    target_logger = logging.getLogger('sky.skylet.constants')
    with mock.patch.object(target_logger, 'debug') as mock_debug:
        assert constants.getenv_server_with_legacy(_NEW,
                                                   _LEGACY) == 'legacy-value'
    assert any(
        'deprecated' in (call.args[0] if call.args else '')
        for call in mock_debug.mock_calls), (
            f'no deprecated-hint log call found: {mock_debug.mock_calls}')


def test_returns_new_when_both_set(monkeypatch):
    """When both values are present and disagree, emit a debug hint
    telling the operator to drop the deprecated alias."""
    monkeypatch.setenv(_NEW, 'new-value')
    monkeypatch.setenv(_LEGACY, 'legacy-value')
    target_logger = logging.getLogger('sky.skylet.constants')
    with mock.patch.object(target_logger, 'debug') as mock_debug:
        assert constants.getenv_server_with_legacy(_NEW, _LEGACY) == 'new-value'
    assert any('Remove the deprecated' in (call.args[0] if call.args else '')
               for call in mock_debug.mock_calls), (
                   f'no operator-hint log call found: {mock_debug.mock_calls}')


def test_returns_default_when_neither_set(monkeypatch):
    monkeypatch.delenv(_NEW, raising=False)
    monkeypatch.delenv(_LEGACY, raising=False)
    assert constants.getenv_server_with_legacy(_NEW, _LEGACY) is None
    assert constants.getenv_server_with_legacy(_NEW, _LEGACY,
                                               default='x') == 'x'


def test_bool_helper(monkeypatch):
    monkeypatch.delenv(_NEW, raising=False)
    monkeypatch.delenv(_LEGACY, raising=False)
    assert constants.getenv_server_with_legacy_bool(_NEW, _LEGACY) is None
    monkeypatch.setenv(_LEGACY, 'true')
    assert constants.getenv_server_with_legacy_bool(_NEW, _LEGACY) is True
    monkeypatch.setenv(_NEW, 'false')
    assert constants.getenv_server_with_legacy_bool(_NEW, _LEGACY) is False
    monkeypatch.setenv(_NEW, '1')
    assert constants.getenv_server_with_legacy_bool(_NEW, _LEGACY) is True
    monkeypatch.setenv(_NEW, 'no')
    assert constants.getenv_server_with_legacy_bool(_NEW, _LEGACY) is False
