import importlib.metadata
import types
from unittest import mock

import pytest
import requests

from sky.adaptors import runpod as runpod_adaptor
from sky.clouds import runpod as runpod_cloud
from sky.clouds.cloud import TeardownExecutionStrategy
from sky.provision.runpod import utils as runpod_utils
from sky.utils import schemas


class _FakeLazyRunPod:

    def __init__(self, module: types.SimpleNamespace) -> None:
        self._module = module

    def load_module(self) -> types.SimpleNamespace:
        return self._module


class _Response:

    def __init__(self, status_code: int, text: str = '') -> None:
        self.status_code = status_code
        self.text = text


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


@pytest.mark.parametrize(
    ('remote_identity', 'expected_strategy'),
    [
        (schemas.RemoteIdentityOptions.LOCAL_CREDENTIALS.value,
         TeardownExecutionStrategy.LEGACY_HEAD_CREDENTIALS),
        (schemas.RemoteIdentityOptions.NO_UPLOAD.value,
         TeardownExecutionStrategy.HEAD_WITH_SERVER_FALLBACK),
    ],
)
def test_runpod_teardown_execution_strategy_for_remote_identity(
        remote_identity, expected_strategy):
    assert (runpod_cloud.RunPod().get_teardown_execution_strategy(
        remote_identity) == expected_strategy)


def test_runpod_default_remote_identity_uses_server_backed_teardown():
    remote_identity = schemas.get_default_remote_identity('runpod')

    assert remote_identity == schemas.RemoteIdentityOptions.NO_UPLOAD.value
    assert (runpod_cloud.RunPod().get_teardown_execution_strategy(
        remote_identity) == TeardownExecutionStrategy.HEAD_WITH_SERVER_FALLBACK)


def _set_pod_identity(monkeypatch) -> None:
    monkeypatch.setenv('RUNPOD_POD_ID', 'xedezhzb9la3ye')
    monkeypatch.setenv('RUNPOD_API_KEY', 'pod-api-key')


def _mock_http_request(monkeypatch, side_effect):
    if isinstance(side_effect, list):
        request = mock.Mock(side_effect=side_effect)
    else:
        request = mock.Mock(return_value=side_effect)
    monkeypatch.setattr(runpod_adaptor.requests, 'request', request)
    return request


def _mock_retry_sleep(monkeypatch):
    sleep = mock.Mock()
    monkeypatch.setattr(runpod_adaptor.time, 'sleep', sleep)
    return sleep


@pytest.mark.parametrize('status_code', [200, 204, 404, 410])
def test_terminate_current_pod_deletes_pod_with_idempotent_success(
        monkeypatch, status_code):
    _set_pod_identity(monkeypatch)
    request = _mock_http_request(monkeypatch, _Response(status_code))

    assert runpod_adaptor.terminate_current_pod() is None

    request.assert_called_once_with(
        'DELETE',
        'https://rest.runpod.io/v1/pods/xedezhzb9la3ye',
        headers={'Authorization': 'Bearer pod-api-key'},
        timeout=10,
    )


@pytest.mark.parametrize('missing_variable',
                         ['RUNPOD_POD_ID', 'RUNPOD_API_KEY'])
def test_terminate_current_pod_requires_pod_identity(monkeypatch,
                                                     missing_variable):
    _set_pod_identity(monkeypatch)
    monkeypatch.delenv(missing_variable)
    request = _mock_http_request(monkeypatch, _Response(204))

    with pytest.raises(RuntimeError, match=missing_variable):
        runpod_adaptor.terminate_current_pod()

    request.assert_not_called()


@pytest.mark.parametrize(
    ('pod_id', 'encoded_pod_id'),
    [
        ('AbC123-_~.opaque', 'AbC123-_~.opaque'),
        ('Pod-Id_42~candidate', 'Pod-Id_42~candidate'),
        ('alt:pod@id', 'alt%3Apod%40id'),
    ],
)
def test_terminate_current_pod_uses_safe_opaque_pod_identity(
        monkeypatch, pod_id, encoded_pod_id):
    _set_pod_identity(monkeypatch)
    monkeypatch.setenv('RUNPOD_POD_ID', pod_id)
    request = _mock_http_request(monkeypatch, _Response(204))

    assert runpod_adaptor.terminate_current_pod() is None

    request.assert_called_once_with(
        'DELETE',
        f'https://rest.runpod.io/v1/pods/{encoded_pod_id}',
        headers={'Authorization': 'Bearer pod-api-key'},
        timeout=10,
    )


@pytest.mark.parametrize(
    'pod_id',
    [
        '.',
        '..',
        '../other',
        'safe/other',
        'safe\\other',
        'safe?query=true',
        'safe#fragment',
        'safe value',
        'safe\nvalue',
        'a' * 129,
    ],
)
def test_terminate_current_pod_rejects_malformed_pod_identity(
        monkeypatch, pod_id):
    _set_pod_identity(monkeypatch)
    monkeypatch.setenv('RUNPOD_POD_ID', pod_id)
    request = _mock_http_request(monkeypatch, _Response(204))

    with pytest.raises(RuntimeError) as error:
        runpod_adaptor.terminate_current_pod()

    expected_error = ('RunPod self-termination requires a valid '
                      'RUNPOD_POD_ID.')
    assert str(error.value) == expected_error
    request.assert_not_called()


@pytest.mark.parametrize(
    'responses',
    [
        [requests.ConnectionError('transient network failure'),
         _Response(204)],
        [requests.Timeout('transient timeout'),
         _Response(204)],
        [_Response(429), _Response(204)],
        [_Response(503), _Response(204)],
    ],
)
def test_terminate_current_pod_retries_transient_failures(
        monkeypatch, responses):
    _set_pod_identity(monkeypatch)
    request = _mock_http_request(monkeypatch, responses)
    sleep = _mock_retry_sleep(monkeypatch)

    assert runpod_adaptor.terminate_current_pod() is None

    assert request.call_count == 2
    sleep.assert_called_once_with(1)


def test_terminate_current_pod_does_not_retry_terminal_request_error(
        monkeypatch):
    _set_pod_identity(monkeypatch)
    request = _mock_http_request(
        monkeypatch,
        [requests.exceptions.InvalidURL('pod-api-key should not leak')],
    )

    with pytest.raises(RuntimeError) as error:
        runpod_adaptor.terminate_current_pod()

    assert str(error.value) == (
        'RunPod self-termination failed due to a request error.')
    request.assert_called_once()


@pytest.mark.parametrize('status_code', [400, 401, 403])
def test_terminate_current_pod_sanitizes_terminal_error(monkeypatch,
                                                        status_code):
    _set_pod_identity(monkeypatch)
    request = _mock_http_request(
        monkeypatch,
        _Response(status_code,
                  'body contains pod-api-key and ?secret=should-not-leak'),
    )

    with pytest.raises(RuntimeError) as error:
        runpod_adaptor.terminate_current_pod()

    assert str(error.value) == (
        f'RunPod self-termination failed with status {status_code}.')
    request.assert_called_once()


def test_terminate_current_pod_bounds_transient_status_retries(monkeypatch):
    _set_pod_identity(monkeypatch)
    request = _mock_http_request(
        monkeypatch,
        [_Response(503)] * runpod_adaptor._MAX_RETRIES,
    )
    sleep = _mock_retry_sleep(monkeypatch)

    with pytest.raises(RuntimeError) as error:
        runpod_adaptor.terminate_current_pod()

    assert str(error.value) == 'RunPod self-termination failed with status 503.'
    assert request.call_count == runpod_adaptor._MAX_RETRIES
    assert sleep.call_args_list == [mock.call(1), mock.call(1)]


def test_terminate_current_pod_does_not_retry_invalid_http_status(monkeypatch):
    _set_pod_identity(monkeypatch)
    request = _mock_http_request(monkeypatch, _Response(600))

    with pytest.raises(RuntimeError) as error:
        runpod_adaptor.terminate_current_pod()

    assert str(error.value) == 'RunPod self-termination failed with status 600.'
    request.assert_called_once()


def test_terminate_current_pod_bounds_network_retries(monkeypatch):
    _set_pod_identity(monkeypatch)
    request = _mock_http_request(
        monkeypatch,
        [requests.ConnectionError('network failure')] *
        runpod_adaptor._MAX_RETRIES,
    )
    sleep = _mock_retry_sleep(monkeypatch)

    with pytest.raises(RuntimeError) as error:
        runpod_adaptor.terminate_current_pod()

    assert str(
        error.value) == 'RunPod self-termination failed due to a network error.'
    assert request.call_count == runpod_adaptor._MAX_RETRIES
    assert sleep.call_args_list == [mock.call(1), mock.call(1)]
