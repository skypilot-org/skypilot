"""Unit tests for the validation /volumes/apply enforces.

These use the real cloud registry rather than a mocked cloud: the point is that
the actual rules apply on the write path, not that a mocked verdict is
forwarded.
"""

import json
from unittest import mock

import fastapi
from fastapi.testclient import TestClient
import pytest
import requests

from sky import exceptions
from sky import models
from sky.server import common as server_common
from sky.server.requests import executor
from sky.utils import infra_utils
from sky.utils import volume
from sky.volumes import volume as volume_lib
from sky.volumes.client import sdk as volumes_sdk
from sky.volumes.server import server


def _pvc_body(**overrides):
    body = {
        'name': 'ok-vol3',
        'volume_type': volume.VolumeType.PVC.value,
        'cloud': 'kubernetes',
        'region': 'my-context',
        'size': '100Gi',
        'config': {
            'access_mode': volume.VolumeAccessMode.READ_WRITE_MANY.value,
        },
    }
    body.update(overrides)
    return body


def _hostpath_body(**overrides):
    body = {
        'name': 'ok-hostpath',
        'volume_type': volume.VolumeType.HOSTPATH.value,
        'cloud': 'kubernetes',
        'region': 'my-context',
        'config': {
            'host_path': '/mnt/data'
        },
    }
    body.update(overrides)
    return body


def _runpod_body(**overrides):
    body = {
        'name': 'ok-runpod',
        'volume_type': volume.VolumeType.RUNPOD_NETWORK_VOLUME.value,
        'cloud': 'runpod',
        'zone': 'CA-MTL-1',
        'size': '100',
        'config': {},
    }
    body.update(overrides)
    return body


@pytest.fixture
def client_and_executor(monkeypatch):
    """A /volumes test client plus the mocked executor it would enqueue to."""
    scheduled = mock.AsyncMock()
    monkeypatch.setattr(executor, 'schedule_request_async', scheduled)
    app = fastapi.FastAPI()
    app.include_router(server.router, prefix='/volumes')
    return TestClient(app), scheduled


def _http_response(status, json_body=None, text=''):
    """A response whose raise_for_status behaves like the real one."""
    response = mock.MagicMock(spec=requests.Response)
    response.status_code = status
    response.url = 'http://test/volumes/apply'
    response.text = text if json_body is None else json.dumps(json_body)
    response.headers = {}
    if json_body is None:
        response.json.side_effect = ValueError('not JSON')
    else:
        response.json.return_value = json_body
    if status >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(
            f'{status} Client Error', response=response)
    return response


def post_apply(client, body):
    with mock.patch.object(fastapi.Request, 'state') as mock_state:
        mock_state.request_id = 'test-request-id'
        mock_state.auth_user = None
        return client.post('/volumes/apply', json=body)


class TestVolumeApplyValidation:
    """/volumes/apply rejects what /volumes/validate rejects."""

    @staticmethod
    def _detail_message(response):
        # /apply returns a serialized exception for volume errors and a bare
        # string for its own type/cloud checks.
        detail = response.json()['detail']
        if isinstance(detail, str):
            return detail
        return detail.get('message', str(detail))

    @pytest.mark.parametrize(
        'body,expected',
        [
            # The bug this file exists for: a name the CLI refuses. The
            # message has to name the volume, not just recite the rule.
            (_pvc_body(name='ok_vol3'), "'ok_vol3'"),
            (_pvc_body(name='ok_vol3'), 'DNS-1123'),
            (_pvc_body(name='UPPER'), 'DNS-1123'),
            (_pvc_body(name='-leading-dash'), 'DNS-1123'),
            (_pvc_body(name=''), 'Volume name must be set'),
            # A new volume needs a size.
            (_pvc_body(size=None), 'Size is required for new volumes'),
            # Labels are checked against the cloud.
            (_pvc_body(labels={'bad key': 'v'}), 'Invalid label'),
            # hostPath: absolute, and not the node root.
            (_hostpath_body(config={'host_path': '/'}), 'root directory'),
            # Absolute paths that resolve to the root: a literal '/' check
            # misses these.
            (_hostpath_body(config={'host_path': '/..'}), 'root directory'),
            (_hostpath_body(config={'host_path': '/mnt/../..'}),
             'root directory'),
            (_hostpath_body(config={'host_path': '/./..'}), 'root directory'),
            (_hostpath_body(config={'host_path': '/../../..'}),
             'root directory'),
            # normpath keeps exactly two leading slashes, but Linux reads '//'
            # as the root.
            (_hostpath_body(config={'host_path': '//'}), 'root directory'),
            (_hostpath_body(config={'host_path': '//..'}), 'root directory'),
            (_hostpath_body(config={'host_path': '//.'}), 'root directory'),
            (_hostpath_body(config={'host_path': 'relative/path'}),
             'absolute path'),
            (_hostpath_body(config={}), 'host_path is required'),
            # RunPod carries the DataCenterId in the zone.
            (_runpod_body(zone=None), 'DataCenterId is required'),
        ])
    def test_invalid_volume_is_rejected(self, client_and_executor, body,
                                        expected):
        client, scheduled = client_and_executor
        response = post_apply(client, body)
        assert response.status_code == 400, response.text
        assert expected in self._detail_message(response)
        # Rejected before a request row is created.
        scheduled.assert_not_called()

    @pytest.mark.parametrize('sent,applied', [
        ('100', '100'),
        ('100Gi', '100'),
        ('1Ti', '1024'),
        ('2048Mi', '2'),
    ])
    def test_size_forwarded_is_the_size_validated(self, client_and_executor,
                                                  sent, applied):
        # _get_pvc_spec appends 'Gi', so forwarding a unit-carrying size
        # verbatim would build an unusable quantity like '100GiGi'.
        client, scheduled = client_and_executor
        response = post_apply(client, _pvc_body(size=sent))
        assert response.status_code == 200, response.text
        body = scheduled.call_args[1]['request_body']
        assert body.size == applied

    @pytest.mark.parametrize('sent', [None, 'omitted'])
    def test_defaulted_config_reaches_the_worker(self, client_and_executor,
                                                 sent):
        # The handler defaults access_mode into a dict that is local when the
        # client sends no config, and VolumeConfig rejects None -- so without
        # stamping it back the request dies in the worker.
        client, scheduled = client_and_executor
        body = _pvc_body()
        if sent is None:
            body['config'] = None
        else:
            body.pop('config')
        response = post_apply(client, body)
        assert response.status_code == 200, response.text
        forwarded = scheduled.call_args[1]['request_body'].config
        assert forwarded is not None
        assert forwarded['access_mode'] == (
            volume.VolumeAccessMode.READ_WRITE_ONCE.value)
        # Proves it can actually be turned into a VolumeConfig downstream.
        models.VolumeConfig(name='n',
                            type=volume.VolumeType.PVC.value,
                            cloud='kubernetes',
                            region='r',
                            zone=None,
                            name_on_cloud='noc',
                            size='1Gi',
                            config=forwarded)

    @pytest.mark.parametrize('body', [
        _pvc_body(),
        _pvc_body(name='ok-vol-2'),
        _hostpath_body(),
        _runpod_body(),
        _pvc_body(size=None, use_existing=True),
    ])
    def test_valid_volume_is_accepted(self, client_and_executor, body):
        client, scheduled = client_and_executor
        response = post_apply(client, body)
        assert response.status_code == 200, response.text
        scheduled.assert_called_once()


class TestDottedNameIsAcceptedToday:
    """Pins dot handling so #10514 flipping it is a deliberate edit."""

    def test_dotted_name_still_accepted(self, client_and_executor):
        client, scheduled = client_and_executor
        response = post_apply(client, _pvc_body(name='dotted.name'))
        assert response.status_code == 200, response.text
        scheduled.assert_called_once()


class TestRealDashboardPayloads:
    """Bodies captured from the Add Volume dialog, verbatim.

    The dialog sends explicit nulls for unset optional config fields, which
    hand-written fixtures do not, and which the config schema rejects.
    """

    # Create, new PVC: namespace is null whenever the user does not set one.
    CREATE_PVC = {
        'name': 'ok-0',
        'volume_type': 'k8s-pvc',
        'cloud': 'kubernetes',
        'region': 'a-context',
        'size': '100',
        'config': {
            'storage_class_name': 'standard-rwx',
            'access_mode': 'ReadWriteMany',
            'namespace': None,
        },
        'use_existing': False,
    }

    # Create, hostPath: size is null and cleanup_on_deletion is present.
    CREATE_HOSTPATH = {
        'name': 'ok-host',
        'volume_type': 'k8s-hostpath',
        'cloud': 'kubernetes',
        'region': 'a-context',
        'size': None,
        'config': {
            'host_path': '/mnt/data',
            'cleanup_on_deletion': True
        },
        'use_existing': False,
    }

    # Import an existing PVC: every field is populated from the real PVC.
    IMPORT_PVC = {
        'name': 'imported',
        'volume_type': 'k8s-pvc',
        'cloud': 'kubernetes',
        'region': 'a-context',
        'size': None,
        'config': {
            'storage_class_name': 'standard-rwx',
            'access_mode': 'ReadWriteMany',
            'namespace': 'default',
        },
        'use_existing': True,
    }

    # No storage class chosen yet -- both optional fields arrive as null.
    CREATE_PVC_ALL_NULL = {
        'name': 'ok-1',
        'volume_type': 'k8s-pvc',
        'cloud': 'kubernetes',
        'region': 'a-context',
        'size': '100',
        'config': {
            'storage_class_name': None,
            'access_mode': 'ReadWriteMany',
            'namespace': None,
        },
        'use_existing': False,
    }

    @pytest.mark.parametrize('body', [
        CREATE_PVC,
        CREATE_HOSTPATH,
        IMPORT_PVC,
        CREATE_PVC_ALL_NULL,
    ])
    def test_dialog_payload_is_accepted(self, client_and_executor, body):
        client, scheduled = client_and_executor
        response = post_apply(client, body)
        assert response.status_code == 200, response.text
        scheduled.assert_called_once()

    def test_null_config_fields_are_dropped_not_forwarded(
            self, client_and_executor):
        client, scheduled = client_and_executor
        assert post_apply(client, self.CREATE_PVC).status_code == 200
        forwarded = scheduled.call_args[1]['request_body'].config
        assert 'namespace' not in forwarded
        assert forwarded['storage_class_name'] == 'standard-rwx'


class TestSdkValidateRejection:
    """validate() has nothing after the helper, so it must never return on 400.

    apply() is covered by get_request_id raising afterwards; validate() is not,
    so a 400 it cannot decode would read as "the volume is valid".
    """

    @staticmethod
    def _volume(monkeypatch, response):
        monkeypatch.setattr(server_common, 'check_server_healthy_or_start_fn',
                            lambda *a, **kw: None)
        monkeypatch.setattr(server_common, 'make_authenticated_request',
                            lambda *a, **kw: response)
        return volume_lib.Volume.from_components(
            name='ok-vol',
            type=volume.VolumeType.PVC.value,
            cloud='kubernetes',
            region='my-context',
            size='1Gi')

    def test_raises_the_servers_message(self, monkeypatch):
        detail = exceptions.serialize_exception(ValueError('nope, invalid'))
        vol = self._volume(monkeypatch,
                           _http_response(400, json_body={'detail': detail}))
        with pytest.raises(ValueError, match='nope, invalid'):
            volumes_sdk.validate(vol)

    def test_raises_on_a_non_json_400(self, monkeypatch):
        vol = self._volume(monkeypatch,
                           _http_response(400, text='<html>Bad Request</html>'))
        with pytest.raises(requests.HTTPError):
            volumes_sdk.validate(vol)

    def test_raises_when_the_400_has_no_detail(self, monkeypatch):
        vol = self._volume(monkeypatch,
                           _http_response(400, json_body={'oops': 'no detail'}))
        with pytest.raises(requests.HTTPError):
            volumes_sdk.validate(vol)

    def test_returns_on_a_200(self, monkeypatch):
        vol = self._volume(monkeypatch, _http_response(200, json_body={}))
        assert volumes_sdk.validate(vol) is None


class TestSyncRejectionAlwaysRaises:
    """The helper's promise must not depend on handle_request_error raising."""

    def test_raises_even_if_the_fallback_returns(self, monkeypatch):
        # handle_request_error raises for every non-200 today. If a later change
        # gives it an early return for some status, the helper must still raise
        # rather than silently reporting an invalid volume as valid.
        monkeypatch.setattr(server_common, 'handle_request_error',
                            lambda response: None)
        response = _http_response(400, text='<html>Bad Request</html>')
        with pytest.raises(RuntimeError, match='Unknown server error'):
            server_common.raise_if_rejected_synchronously(response)

    def test_returns_for_a_non_400(self, monkeypatch):
        called = []
        monkeypatch.setattr(server_common, 'handle_request_error',
                            lambda response: called.append(1))
        assert server_common.raise_if_rejected_synchronously(
            _http_response(200, json_body={})) is None
        assert not called


class TestFromComponents:
    """from_components must preserve the resolved cloud/region/zone exactly."""

    @pytest.mark.parametrize(
        'cloud,region,zone,vol_type,size',
        [
            ('kubernetes', 'my-context', None, volume.VolumeType.PVC.value,
             '1Gi'),
            ('kubernetes', 'arn:aws:eks:us-west-2:1:cluster/prod', None,
             volume.VolumeType.PVC.value, '1Gi'),
            # An SSH node pool is a context named ssh-<pool>. Serializing this
            # through an infra string dropped the prefix.
            ('kubernetes', 'ssh-mypool', None, volume.VolumeType.PVC.value,
             '1Gi'),
            ('kubernetes', None, None, volume.VolumeType.PVC.value, '1Gi'),
            ('runpod', None, 'CA-MTL-1',
             volume.VolumeType.RUNPOD_NETWORK_VOLUME.value, '100'),
            ('runpod', 'us-east', 'CA-MTL-1',
             volume.VolumeType.RUNPOD_NETWORK_VOLUME.value, '100'),
        ])
    def test_resolved_components_survive(self, cloud, region, zone, vol_type,
                                         size):
        vol = volume_lib.Volume.from_components(name='ok-vol',
                                                type=vol_type,
                                                cloud=cloud,
                                                region=region,
                                                zone=zone,
                                                size=size)
        vol.validate()
        assert (vol.cloud, vol.region, vol.zone) == (cloud, region, zone)


class TestSdkApplyRejection:
    """The SDK surfaces a synchronous rejection as the server's error."""

    @staticmethod
    def _response(detail):
        return _http_response(400, json_body={'detail': detail})

    def _volume(self, monkeypatch, detail):
        # The SDK entrypoint would otherwise try to start a real API server.
        monkeypatch.setattr(server_common, 'check_server_healthy_or_start_fn',
                            lambda *a, **kw: None)
        monkeypatch.setattr(server_common, 'make_authenticated_request',
                            lambda *a, **kw: self._response(detail))
        vol = volume_lib.Volume.from_components(
            name='ok-vol',
            type=volume.VolumeType.PVC.value,
            cloud='kubernetes',
            region='my-context',
            size='1Gi')
        return vol

    def test_serialized_exception_detail(self, monkeypatch):
        detail = exceptions.serialize_exception(
            ValueError('Invalid volume name: nope'))
        vol = self._volume(monkeypatch, detail)
        with pytest.raises(ValueError, match='Invalid volume name'):
            volumes_sdk.apply(vol)

    def test_non_json_400_keeps_the_normal_error_path(self, monkeypatch):
        # The guard exists for a 400 from something that is not this handler --
        # a proxy returning an HTML error page. It must not become a
        # JSONDecodeError, which would be worse than the HTTPError it replaced.
        monkeypatch.setattr(server_common, 'check_server_healthy_or_start_fn',
                            lambda *a, **kw: None)
        response = _http_response(400, text='<html>Bad Request</html>')
        monkeypatch.setattr(server_common, 'make_authenticated_request',
                            lambda *a, **kw: response)
        vol = volume_lib.Volume.from_components(
            name='ok-vol',
            type=volume.VolumeType.PVC.value,
            cloud='kubernetes',
            region='my-context',
            size='1Gi')
        # The fall-through's own error, not a decode error from the guard.
        with pytest.raises(requests.HTTPError):
            volumes_sdk.apply(vol)

    def test_detail_absent_keeps_the_normal_error_path(self, monkeypatch):
        # Valid JSON with no `detail` must fall through too.
        monkeypatch.setattr(server_common, 'check_server_healthy_or_start_fn',
                            lambda *a, **kw: None)
        response = _http_response(400, json_body={'oops': 'no detail here'})
        monkeypatch.setattr(server_common, 'make_authenticated_request',
                            lambda *a, **kw: response)
        vol = volume_lib.Volume.from_components(
            name='ok-vol',
            type=volume.VolumeType.PVC.value,
            cloud='kubernetes',
            region='my-context',
            size='1Gi')
        with pytest.raises(requests.HTTPError):
            volumes_sdk.apply(vol)

    def test_plain_string_detail(self, monkeypatch):
        # The pre-existing 400s on this endpoint return a bare string.
        vol = self._volume(monkeypatch, 'Invalid volume type: nope')
        with pytest.raises(RuntimeError, match='Invalid volume type'):
            volumes_sdk.apply(vol)
