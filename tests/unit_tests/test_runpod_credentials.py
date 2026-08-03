import importlib.metadata
import types

import pytest

from sky.adaptors import runpod as runpod_adaptor
from sky.clouds import runpod as runpod_cloud
from sky.provision.runpod import utils as runpod_utils


class _FakeLazyRunPod:

    def __init__(self, module: types.SimpleNamespace) -> None:
        self._module = module

    def load_module(self) -> types.SimpleNamespace:
        return self._module


def _write_runpod_config(tmp_path, monkeypatch, api_key: str) -> None:
    monkeypatch.setenv('HOME', str(tmp_path))
    credential_dir = tmp_path / '.runpod'
    credential_dir.mkdir()
    (credential_dir / 'config.toml').write_text(
        f'[default]\napi_key = "{api_key}"\n', encoding='utf-8')


def test_credential_check_sets_api_key_on_loaded_sdk(tmp_path,
                                                     monkeypatch) -> None:
    _write_runpod_config(tmp_path, monkeypatch, 'credential-check-key')
    sdk_module = types.SimpleNamespace(api_key=None)
    monkeypatch.setattr(runpod_adaptor, 'runpod', _FakeLazyRunPod(sdk_module))

    valid, error = runpod_cloud.RunPod._check_runpod_credentials()

    assert valid, error
    assert sdk_module.api_key == 'credential-check-key'


def test_provisioning_loads_api_key_from_config(tmp_path, monkeypatch) -> None:
    _write_runpod_config(tmp_path, monkeypatch, 'provisioning-key')
    sdk_module = types.SimpleNamespace(api_key=None)
    monkeypatch.setattr(runpod_adaptor, 'runpod', _FakeLazyRunPod(sdk_module))

    runpod_utils._ensure_api_key_configured()

    assert sdk_module.api_key == 'provisioning-key'


@pytest.mark.parametrize(
    ('installed_version', 'expected_valid'),
    [('1.7.9', False), ('1.7.10', True), ('2.0.0', True)],
)
def test_credential_check_enforces_runpod_sdk_minimum(monkeypatch,
                                                      installed_version,
                                                      expected_valid):
    monkeypatch.setattr(runpod_cloud.import_lib_util, 'find_spec',
                        lambda _name: object())
    monkeypatch.setattr(importlib.metadata, 'version',
                        lambda _name: installed_version)
    monkeypatch.setattr(runpod_cloud.RunPod, '_check_runpod_credentials',
                        lambda: (True, None))
    monkeypatch.setattr(runpod_cloud.RunPod, '_validate_api_key', lambda:
                        (True, None))

    valid, error = runpod_cloud.RunPod._check_credentials()

    assert valid is expected_valid
    if not expected_valid:
        assert 'runpod>=1.7.10' in error
