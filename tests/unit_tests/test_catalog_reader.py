"""Regression tests for recovering a cached non-callable catalog reader."""
# pylint: disable=protected-access
import types
from unittest import mock

import pandas as pd
import pytest

from sky.adaptors import common as adaptors_common
from sky.catalog import common as catalog_common
from sky.utils import annotations


@pytest.fixture(params=['import', 'missing-attribute'])
def cached_reader_proxy(monkeypatch, request):
    """Reproduce a cached submodule proxy while native pandas is healthy."""
    proxy = adaptors_common.LazyImport('pandas')
    if request.param == 'import':
        with mock.patch.object(adaptors_common.importlib,
                               'import_module',
                               side_effect=AttributeError('transient import')):
            assert isinstance(proxy.read_csv, adaptors_common.LazyImport)
    else:
        incomplete = types.ModuleType('pandas')
        proxy._module = incomplete
        assert isinstance(proxy.read_csv, adaptors_common.LazyImport)
        incomplete.read_csv = pd.read_csv
    assert callable(pd.read_csv)
    assert isinstance(proxy.read_csv, adaptors_common.LazyImport)
    monkeypatch.setattr(catalog_common, 'pd', proxy)


@pytest.mark.usefixtures('cached_reader_proxy')
def test_catalog_recovers_cached_reader(tmp_path):
    """A cached lazy submodule must not prevent loading an existing CSV."""
    path = tmp_path / 'catalog.csv'
    path.write_text('InstanceType,Price\nexample,0.25\n')
    frame = catalog_common.LazyDataFrame(str(path), lambda: False)
    pd.testing.assert_frame_equal(frame._load_df(), pd.read_csv(path))


@pytest.mark.usefixtures('cached_reader_proxy')
def test_catalog_recovered_reader_preserves_parse_errors(tmp_path):
    """A failed parse remains retryable after the CSV has been repaired."""
    path = tmp_path / 'catalog.csv'
    path.write_text('Price\n"unterminated')
    frame = catalog_common.LazyDataFrame(str(path), lambda: False)
    with pytest.raises(pd.errors.ParserError):
        frame._load_df()
    path.write_text('Price\n0.25\n')
    pd.testing.assert_frame_equal(frame._load_df(), pd.read_csv(path))


def test_catalog_preserves_callable_reader_and_cache(monkeypatch, tmp_path):
    """Callable readers keep request caching and stale-file refresh behavior."""
    proxy = adaptors_common.LazyImport('pandas')
    reader = mock.Mock(wraps=pd.read_csv)
    proxy.read_csv = reader
    monkeypatch.setattr(catalog_common, 'pd', proxy)
    native = mock.Mock(side_effect=AssertionError('unexpected fallback'))
    monkeypatch.setattr(pd, 'read_csv', native)
    path = tmp_path / 'catalog.csv'
    path.write_text('Price\n1\n')
    update = mock.Mock(return_value=False)
    frame = catalog_common.LazyDataFrame(str(path), update)
    original = frame._load_df()
    assert frame._load_df() is original
    assert reader.call_count == update.call_count == 1
    annotations.clear_request_level_cache()
    assert frame._load_df() is original
    assert reader.call_count == 1 and update.call_count == 2
    path.write_text('Price\n2\n')
    update.return_value = True
    annotations.clear_request_level_cache()
    assert frame._load_df().iloc[0]['Price'] == 2
    assert original.iloc[0]['Price'] == 1
    assert reader.call_count == 2 and update.call_count == 3
    native.assert_not_called()


@pytest.mark.parametrize('error_type', [AttributeError, ValueError])
def test_catalog_does_not_retry_callable_errors(monkeypatch, error_type):
    """Reader failures must not be mistaken for a lazy import failure."""
    proxy = adaptors_common.LazyImport('pandas')
    error = error_type('reader failed')
    reader = mock.Mock(side_effect=error)
    proxy.read_csv = reader
    monkeypatch.setattr(catalog_common, 'pd', proxy)
    native = mock.Mock(side_effect=AssertionError('unexpected fallback'))
    monkeypatch.setattr(pd, 'read_csv', native)
    frame = catalog_common.LazyDataFrame('catalog.csv', lambda: False)
    with pytest.raises(error_type) as caught:
        frame._load_df()
    assert caught.value is error
    reader.assert_called_once_with('catalog.csv')
    native.assert_not_called()


def test_catalog_does_not_hide_unexpected_noncallable(monkeypatch):
    """Only a cached LazyImport reader qualifies for the fallback."""
    proxy = adaptors_common.LazyImport('pandas')
    proxy.read_csv = None
    monkeypatch.setattr(catalog_common, 'pd', proxy)
    native = mock.Mock(side_effect=AssertionError('unexpected fallback'))
    monkeypatch.setattr(pd, 'read_csv', native)
    frame = catalog_common.LazyDataFrame('catalog.csv', lambda: False)
    with pytest.raises(TypeError):
        frame._load_df()
    native.assert_not_called()
