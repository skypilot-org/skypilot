"""Tests for optional C acceleration in YAML loaders."""
import io
import os
import stat
import sys
import types

import pytest
import yaml

from sky.adaptors import common
from sky.utils import yaml_utils


@pytest.fixture(name='loader_variant', params=['c', 'missing', 'noncallable'])
def fixture_loader_variant(request, monkeypatch):
    module = types.ModuleType('yaml')
    module.__dict__.update(vars(yaml))
    calls = []
    if request.param == 'c' and not hasattr(yaml, 'CSafeLoader'):
        pytest.skip('PyYAML C extension is not installed')
    base_loader = yaml.CSafeLoader if request.param == 'c' else yaml.SafeLoader

    class RecordingLoader(base_loader):

        def __init__(self, stream):
            calls.append(request.param)
            super().__init__(stream)

    if request.param == 'c':
        module.CSafeLoader = RecordingLoader
    else:
        module.SafeLoader = RecordingLoader
        if request.param == 'missing':
            module.__dict__.pop('CSafeLoader', None)
        else:
            module.CSafeLoader = None
    monkeypatch.setitem(sys.modules, 'yaml', module)
    monkeypatch.setattr(yaml_utils, 'yaml', common.LazyImport('yaml'))
    monkeypatch.setattr(yaml_utils, '_c_extension_unavailable', False)
    return request.param, calls


def test_safe_load_optional_c_extension(loader_variant):
    variant, calls = loader_variant
    # Repeated file-like reads also exercise the cached fallback.
    for _ in range(2):
        assert yaml_utils.safe_load(io.StringIO('key: [1, true, null]')) == {
            'key': [1, True, None]
        }
    assert calls == [variant, variant]


def test_safe_load_all_optional_c_extension(loader_variant):
    variant, calls = loader_variant
    stream = io.StringIO('key: 1\n---\nkey: 2\n')
    documents = yaml_utils.safe_load_all(stream)
    assert stream.tell() == 0
    assert list(documents) == [{'key': 1}, {'key': 2}]
    assert calls == [variant]


@pytest.mark.parametrize('load_all', [False, True])
@pytest.mark.usefixtures('loader_variant')
def test_optional_c_extension_rejects_python_objects(load_all):
    payload = '!!python/object/apply:builtins.eval ["1 + 1"]'
    with pytest.raises(yaml.constructor.ConstructorError):
        if load_all:
            list(yaml_utils.safe_load_all(payload))
        else:
            yaml_utils.safe_load(payload)


@pytest.mark.parametrize('load_all', [False, True])
@pytest.mark.parametrize('error', [TypeError, AttributeError])
def test_loader_errors_are_not_retried(monkeypatch, load_all, error):

    class BrokenReader:

        def read(self, size):
            raise error('reader failure')

    monkeypatch.setattr(yaml_utils, '_c_extension_unavailable', False)
    with pytest.raises(error, match='reader failure'):
        if load_all:
            list(yaml_utils.safe_load_all(BrokenReader()))
        else:
            yaml_utils.safe_load(BrokenReader())
    assert not yaml_utils._c_extension_unavailable  # pylint: disable=protected-access


def test_dump_yaml_atomic_through_a_symlink_keeps_the_link_and_mode(tmp_path):
    target_dir = tmp_path / 'real'
    target_dir.mkdir()
    target = target_dir / 'config.yaml'
    target.write_text('old: 1\n')
    # A mode no umask would produce, so a lost chmod cannot pass by chance.
    os.chmod(target, 0o604)
    link = tmp_path / 'config.yaml'
    link.symlink_to(target)

    yaml_utils.dump_yaml_atomic(str(link), {'new': 2})

    assert link.is_symlink()
    assert os.readlink(link) == str(target)
    assert yaml_utils.read_yaml(str(target)) == {'new': 2}
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o604
    # No temp file is left behind.
    assert os.listdir(target_dir) == ['config.yaml']


def test_dump_yaml_atomic_new_file_gets_the_default_mode(tmp_path):
    umask = os.umask(0o027)
    try:
        yaml_utils.dump_yaml_atomic(str(tmp_path / 'new.yaml'), {'a': 1})
    finally:
        os.umask(umask)
    assert stat.S_IMODE(os.stat(tmp_path / 'new.yaml').st_mode) == 0o640
    assert os.listdir(tmp_path) == ['new.yaml']


def test_dump_yaml_atomic_contents_are_never_more_readable(
        tmp_path, monkeypatch):
    path = tmp_path / 'config.yaml'
    path.write_text('db: postgresql://user:secret@host/db\n')
    os.chmod(path, 0o600)
    modes = []
    real_open = os.open

    def recording_open(*args, **kwargs):
        fd = real_open(*args, **kwargs)
        modes.append(os.fstat(fd).st_mode & 0o777)
        return fd

    monkeypatch.setattr(os, 'open', recording_open)
    umask = os.umask(0o022)
    try:
        yaml_utils.dump_yaml_atomic(str(path), {'db': 'postgresql://other'})
    finally:
        os.umask(umask)
    # The temp file has the target's mode from the moment it exists.
    assert modes == [0o600]
    assert os.stat(path).st_mode & 0o777 == 0o600
