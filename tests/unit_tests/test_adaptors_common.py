"""Regression tests for lazy module and attribute resolution."""
from concurrent import futures
import sys
import types
from unittest import mock

import pytest

from sky.adaptors import common


@pytest.mark.parametrize('error_type',
                         [AttributeError, ImportError, RuntimeError])
def test_failed_import_can_retry(error_type):
    module = types.ModuleType('example')
    module.value = object()
    error = error_type('import failed')
    proxy = common.LazyImport('example')
    with mock.patch.object(common.importlib,
                           'import_module',
                           side_effect=[error, module]) as load:
        with pytest.raises(error_type) as caught:
            _ = proxy.value
        assert caught.value is error
        assert 'value' not in proxy.__dict__
        assert proxy.value is module.value
        assert proxy.value is module.value
    assert load.call_count == 2


def test_missing_attribute_can_appear_later():
    module = types.ModuleType('example')
    proxy = common.LazyImport('example')
    error = AttributeError('value unavailable')
    module.__getattr__ = mock.Mock(side_effect=error)
    with mock.patch.object(common.importlib,
                           'import_module',
                           return_value=module):
        with pytest.raises(AttributeError) as caught:
            _ = proxy.value
        assert caught.value is error
        assert not hasattr(proxy, 'value')
        assert 'value' not in proxy.__dict__
        module.value = object()
        assert proxy.value is module.value


@pytest.mark.parametrize('namespace', [False, True])
def test_real_submodule_stays_lazy(monkeypatch, tmp_path, namespace):
    package_name = f'lazy_example_{namespace}'
    package = tmp_path / package_name
    package.mkdir()
    if not namespace:
        (package / '__init__.py').write_text('')
    (package / 'child.py').write_text('value = 42\n')
    monkeypatch.syspath_prepend(str(tmp_path))
    proxy = common.LazyImport(package_name)
    try:
        assert package_name not in sys.modules
        child = proxy.child
        assert isinstance(child, common.LazyImport)
        assert f'{package_name}.child' not in sys.modules
        assert child is proxy.child
        assert child.value == 42
        assert f'{package_name}.child' in sys.modules
        assert not hasattr(proxy, 'missing')
        assert 'missing' not in proxy.__dict__
    finally:
        sys.modules.pop(f'{package_name}.child', None)
        sys.modules.pop(package_name, None)


def test_optional_dependency_keeps_install_message():
    error = ModuleNotFoundError('dependency missing')
    proxy = common.LazyImport('example', import_error_message='install example')
    with mock.patch.object(common.importlib, 'import_module',
                           side_effect=error):
        with pytest.raises(ImportError, match='install example') as caught:
            _ = proxy.value
    assert caught.value.__cause__ is error
    assert 'value' not in proxy.__dict__


def test_concurrent_readers_share_one_import():
    module = types.ModuleType('example')
    module.value = object()
    setup = mock.Mock()
    proxy = common.LazyImport('example', set_loggers=setup)
    with mock.patch.object(common.importlib,
                           'import_module',
                           return_value=module) as load:
        with futures.ThreadPoolExecutor(max_workers=8) as executor:
            values = list(executor.map(lambda _: proxy.value, range(32)))
    assert all(value is module.value for value in values)
    load.assert_called_once_with('example')
    setup.assert_called_once_with()


@pytest.mark.parametrize('loaded', [False, True])
def test_submodule_without_spec(monkeypatch, loaded):
    """Modules supplied through sys.modules need not have import specs."""
    package = types.ModuleType('example')
    package.__path__ = []
    child = types.ModuleType('example.child')
    child.value = 42
    monkeypatch.setitem(sys.modules, 'example', package)
    monkeypatch.setitem(sys.modules, 'example.child', child if loaded else None)
    proxy = common.LazyImport('example')
    if loaded:
        assert proxy.child.value == 42
    else:
        with pytest.raises(AttributeError):
            _ = proxy.child
        assert 'child' not in proxy.__dict__
