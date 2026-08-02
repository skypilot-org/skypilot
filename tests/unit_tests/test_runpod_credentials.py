import types

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
