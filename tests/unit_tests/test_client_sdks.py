"""Unit tests for the ``sky.client_sdks`` namespace entry-point loader.

These exercise the lazy resolution of client SDKs that installed packages can
register under the ``sky`` namespace (e.g. ``sky.foo``) via the
``sky.client_sdks`` entry-point group, without SkyPilot knowing about them at
build time.
"""
# fake_sdk is a pytest fixture; receiving it as an argument is intentional.
# pylint: disable=redefined-outer-name
import importlib
import importlib.metadata
import sys
import types

import pytest

import sky

_SDK_NAME = 'fakesdk'
_TARGET_MODULE = 'sky_fake_sdk_target'


def _make_entry_point():
    return importlib.metadata.EntryPoint(_SDK_NAME, _TARGET_MODULE,
                                         'sky.client_sdks')


class _SelectableEntryPoints(list):
    """Minimal stand-in for the ``entry_points()`` result on Python 3.10+."""

    def select(self, *, group):
        return [ep for ep in self if ep.group == group]


@pytest.fixture
def fake_sdk(monkeypatch):
    """Register a fake client SDK and the target module it aliases to."""
    target = types.ModuleType(_TARGET_MODULE)
    target.create = lambda: 'created'  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, _TARGET_MODULE, target)

    def _entry_points():
        return _SelectableEntryPoints([_make_entry_point()])

    monkeypatch.setattr(importlib.metadata, 'entry_points', _entry_points)
    yield target
    # The loader caches resolution in sys.modules and as an attribute on the
    # ``sky`` module; undo both so each test starts from a cold state.
    sys.modules.pop(f'sky.{_SDK_NAME}', None)
    if hasattr(sky, _SDK_NAME):
        delattr(sky, _SDK_NAME)


def test_attribute_access_resolves_sdk(fake_sdk):
    # ``import sky; sky.fakesdk`` (bare attribute access via __getattr__).
    resolved = getattr(sky, _SDK_NAME)
    assert resolved is fake_sdk
    assert resolved.create() == 'created'


def test_dotted_import_resolves_sdk(fake_sdk):
    # Cold ``import sky.fakesdk`` (dotted submodule form via the meta-path
    # finder); the alias must be the same module object, not a re-execution.
    module = importlib.import_module(f'sky.{_SDK_NAME}')
    assert module is fake_sdk
    assert sys.modules[f'sky.{_SDK_NAME}'] is fake_sdk


def test_from_import_resolves_sdk(fake_sdk):
    # ``from sky import fakesdk`` (exercises the from-list import machinery).
    module = __import__('sky', fromlist=[_SDK_NAME])
    assert getattr(module, _SDK_NAME) is fake_sdk


@pytest.mark.usefixtures('fake_sdk')
def test_unknown_name_raises():
    with pytest.raises(AttributeError):
        getattr(sky, 'not_a_registered_sdk')
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module('sky.not_a_registered_sdk')


def test_underscore_names_are_not_resolved():
    # Dunder / private probes must short-circuit before any entry-point scan.
    with pytest.raises(AttributeError):
        getattr(sky, '_definitely_private')


@pytest.mark.usefixtures('fake_sdk')
def test_find_entry_point_select_api():
    # pylint: disable=protected-access
    entry_point = sky._find_client_sdk_entry_point(_SDK_NAME)
    assert entry_point is not None
    assert entry_point.value == _TARGET_MODULE
    assert sky._find_client_sdk_entry_point('missing') is None


def test_find_entry_point_legacy_mapping(monkeypatch):
    # On Python < 3.10 ``entry_points()`` returns a plain mapping with no
    # ``.select`` method, so the loader falls back to ``.get(group)``.
    def _legacy_entry_points():
        return {'sky.client_sdks': [_make_entry_point()]}

    monkeypatch.setattr(importlib.metadata, 'entry_points',
                        _legacy_entry_points)
    # pylint: disable=protected-access
    entry_point = sky._find_client_sdk_entry_point(_SDK_NAME)
    assert entry_point is not None
    assert entry_point.value == _TARGET_MODULE
