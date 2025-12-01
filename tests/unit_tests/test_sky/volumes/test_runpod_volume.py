"""Test RunPod network volume."""
from unittest.mock import MagicMock

import pytest

from sky.adaptors import runpod
from sky.provision.runpod import volume as runpod_prov
from sky.volumes import volume as volume_lib


class TestRunPodVolume:

    def _mock_infra(self, monkeypatch, zone=None):
        mock_infra_info = MagicMock()
        mock_infra_info.cloud = 'runpod'
        mock_infra_info.region = None
        mock_infra_info.zone = zone
        monkeypatch.setattr('sky.utils.infra_utils.InfraInfo.from_str',
                            lambda x: mock_infra_info)
        # Bypass provider-specific zone validation
        monkeypatch.setattr('sky.clouds.runpod.RunPod.validate_region_zone',
                            lambda self, r, z: (r, z),
                            raising=True)

    def test_factory_returns_runpod_subclass(self, monkeypatch):
        self._mock_infra(monkeypatch, zone='ca-mtl-1')
        cfg = {
            'name': 'rpv',
            'type': 'runpod-network-volume',
            'infra': 'runpod/ca/CA-MTL-1',
            'size': '100'
        }
        vol = volume_lib.Volume.from_yaml_config(cfg)
        assert vol.type == 'runpod-network-volume'
        assert type(vol).__name__ in ('RunpodNetworkVolume',)

    def test_factory_invalid_type_raises(self):
        cfg = {
            'name': 'rpv',
            'type': 'runpod-network-vol',  # typo
            'infra': 'runpod/iad-1',
            'size': '100'
        }
        with pytest.raises(ValueError) as exc_info:
            _ = volume_lib.Volume.from_yaml_config(cfg)
        assert 'Invalid volume type' in str(exc_info.value)

    def test_factory_missing_type_raises(self):
        cfg = {'name': 'rpv', 'infra': 'runpod/iad-1', 'size': '100'}
        with pytest.raises(ValueError) as exc_info:
            _ = volume_lib.Volume.from_yaml_config(cfg)
        assert 'Invalid volume type' in str(exc_info.value)

    def test_factory_wrong_case_type_raises(self):
        cfg = {
            'name': 'rpv',
            'type': 'RUNPOD-NETWORK-VOLUME',
            'infra': 'runpod/iad-1',
            'size': '100'
        }
        with pytest.raises(ValueError) as exc_info:
            _ = volume_lib.Volume.from_yaml_config(cfg)
        assert 'Invalid volume type' in str(exc_info.value)

    def test_normalize_success_with_zone_and_min_size(self, monkeypatch):
        self._mock_infra(monkeypatch, zone='iad-1')
        cfg = {
            'name': 'rpv',
            'type': 'runpod-network-volume',
            'infra': 'runpod/iad-1',
            'size': '100'
        }
        vol = volume_lib.Volume.from_yaml_config(cfg)
        assert vol.cloud == 'runpod'
        assert vol.zone == 'iad-1'

    def test_missing_zone_raises(self, monkeypatch):
        self._mock_infra(monkeypatch, zone=None)
        cfg = {
            'name': 'rpv',
            'type': 'runpod-network-volume',
            'infra': 'runpod',
            'size': '100'
        }
        with pytest.raises(ValueError) as exc_info:
            vol = volume_lib.Volume.from_yaml_config(cfg)
            vol.validate()
        assert 'RunPod DataCenterId is required for network volumes' in str(
            exc_info.value)

    def test_min_size_enforced(self, monkeypatch):
        from sky.utils import volume as utils_volume
        self._mock_infra(monkeypatch, zone='iad-1')
        too_small = max(1, utils_volume.MIN_RUNPOD_NETWORK_VOLUME_SIZE_GB - 1)
        cfg = {
            'name': 'rpv',
            'type': 'runpod-network-volume',
            'infra': 'runpod/iad-1',
            'size': str(too_small)
        }
        with pytest.raises(ValueError) as exc_info:
            vol = volume_lib.Volume.from_yaml_config(cfg)
            vol.validate()
        assert 'RunPod network volume size must be at least' in str(
            exc_info.value)

    def test_cli_overrides_applied(self, monkeypatch):
        self._mock_infra(monkeypatch, zone='iad-1')
        cfg = {
            'name': 'new',
            'infra': 'runpod/iad-1',
            'type': 'runpod-network-volume',
            'size': '200',
        }
        vol = volume_lib.Volume.from_yaml_config(cfg)
        assert vol.name == 'new'
        assert vol.type == 'runpod-network-volume'
        assert vol.size == '200'

    def test_invalid_size_pattern_raises(self, monkeypatch):
        self._mock_infra(monkeypatch, zone='iad-1')
        cfg = {
            'name': 'rpv',
            'type': 'runpod-network-volume',
            'infra': 'runpod/iad-1',
            'size': '50Mi'  # invalid in our adjust (expects Gi or integer)
        }
        with pytest.raises(ValueError) as exc_info:
            vol = volume_lib.Volume.from_yaml_config(cfg)
            vol.validate()
        assert 'Invalid size' in str(exc_info.value)

    def test_cloud_mismatch_raises(self, monkeypatch):
        # Infra resolves to kubernetes while type is runpod -> mismatch
        mock_infra_info = MagicMock()
        mock_infra_info.cloud = 'kubernetes'
        mock_infra_info.region = None
        mock_infra_info.zone = None
        monkeypatch.setattr('sky.utils.infra_utils.InfraInfo.from_str',
                            lambda x: mock_infra_info)
        cfg = {
            'name': 'rpv',
            'type': 'runpod-network-volume',
            'infra': 'k8s',
            'size': '100'
        }
        with pytest.raises(ValueError) as exc_info:
            vol = volume_lib.Volume.from_yaml_config(cfg)
            vol.validate()
        assert 'Invalid cloud' in str(exc_info.value)

    def test_resource_name_without_size_ok(self, monkeypatch):
        self._mock_infra(monkeypatch, zone='iad-1')
        cfg = {
            'name': 'rpv',
            'type': 'runpod-network-volume',
            'infra': 'runpod/iad-1',
            'resource_name': 'existing-volume'
        }
        volume_lib.Volume.from_yaml_config(cfg)

    def test_to_yaml_config_contains_cloud_after_normalize(self, monkeypatch):
        self._mock_infra(monkeypatch, zone='iad-1')
        cfg = {
            'name': 'rpv',
            'type': 'runpod-network-volume',
            'infra': 'runpod/us/iad-1',
            'size': '100'
        }
        vol = volume_lib.Volume.from_yaml_config(cfg)
        d = vol.to_yaml_config()
        assert d['cloud'] == 'runpod'
        assert d['zone'] == 'iad-1'

    def test_volume_name_validation(self, monkeypatch):
        self._mock_infra(monkeypatch, zone='iad-1')
        # Over-length name (>30) should fail; no DNS-1123 constraints otherwise
        cfg = {
            'name': 'x' * 31,
            'type': 'runpod-network-volume',
            'infra': 'runpod/iad-1',
            'size': '100'
        }
        with pytest.raises(ValueError) as exc_info:
            vol = volume_lib.Volume.from_yaml_config(cfg)
            vol.validate()
        assert 'Invalid volume name: Volume name exceeds' in str(exc_info.value)
        # Max length boundary (30) should pass
        ok_cfg = {
            'name': 'y' * 30,
            'type': 'runpod-network-volume',
            'infra': 'runpod/iad-1',
            'size': '100'
        }
        volume_lib.Volume.from_yaml_config(ok_cfg)


class TestRunPodProvisionVolume:

    class _Resp:

        def __init__(self,
                     status_code=200,
                     text='',
                     json_obj=None,
                     json_raises=False):
            self.status_code = status_code
            self.text = text
            self._json_obj = json_obj
            self._json_raises = json_raises

        def json(self):
            if self._json_raises:
                raise ValueError('not json')
            return self._json_obj

    def _mock_requests(self, monkeypatch, response):

        class _Req:

            @staticmethod
            def request(method, url, headers=None, json=None, timeout=30):
                return response

        monkeypatch.setattr(runpod, 'requests', _Req)

    def test_get_api_key_from_sdk(self, monkeypatch):

        class _SDK:
            api_key = 'abc'

        monkeypatch.setattr('sky.adaptors.runpod.runpod', _SDK)
        assert runpod._get_api_key() == 'abc'

    def test_get_api_key_from_env(self, monkeypatch):

        class _SDK:
            api_key = None

        monkeypatch.setattr('sky.adaptors.runpod.runpod', _SDK)
        monkeypatch.setenv('RUNPOD_API_KEY', 'envkey')
        assert runpod._get_api_key() == 'envkey'

    def test_get_api_key_missing_raises(self, monkeypatch):

        class _SDK:
            api_key = None

        monkeypatch.setattr('sky.adaptors.runpod.runpod', _SDK)
        monkeypatch.delenv('RUNPOD_API_KEY', raising=False)
        with pytest.raises(RuntimeError):
            _ = runpod._get_api_key()

    def test_rest_request_success_json(self, monkeypatch):

        class _SDK:
            api_key = 'k'

        monkeypatch.setattr('sky.adaptors.runpod.runpod', _SDK)
        resp = self._Resp(status_code=200,
                          text='{"foo":1}',
                          json_obj={'foo': 1})
        self._mock_requests(monkeypatch, resp)
        out = runpod.rest_request('GET', '/networkvolumes')
        assert out == {'foo': 1}

    def test_rest_request_success_plain_text(self, monkeypatch):

        class _SDK:
            api_key = 'k'

        monkeypatch.setattr('sky.adaptors.runpod.runpod', _SDK)
        resp = self._Resp(status_code=200, text='ok', json_raises=True)
        self._mock_requests(monkeypatch, resp)
        out = runpod.rest_request('GET', '/ping')
        assert out == 'ok'

    def test_rest_request_no_text(self, monkeypatch):

        class _SDK:
            api_key = 'k'

        monkeypatch.setattr('sky.adaptors.runpod.runpod', _SDK)
        resp = self._Resp(status_code=200, text='')
        self._mock_requests(monkeypatch, resp)
        out = runpod.rest_request('DELETE', '/networkvolumes/x')
        assert out is None

    def test_rest_request_error_raises(self, monkeypatch):

        class _SDK:
            api_key = 'k'

        monkeypatch.setattr('sky.adaptors.runpod.runpod', _SDK)
        resp = self._Resp(status_code=500, text='boom')
        self._mock_requests(monkeypatch, resp)
        with pytest.raises(RuntimeError):
            _ = runpod.rest_request('GET', '/fail')

    def test_rest_request_retries_then_success_5xx(self, monkeypatch):

        class _SDK:
            api_key = 'k'

        monkeypatch.setattr('sky.adaptors.runpod.runpod', _SDK)

        class SeqReq:
            calls = 0
            seq = [
                TestRunPodProvisionVolume._Resp(status_code=500, text='err1'),
                TestRunPodProvisionVolume._Resp(status_code=500, text='err2'),
                TestRunPodProvisionVolume._Resp(status_code=200,
                                                text='{"ok":true}',
                                                json_obj={'ok': True}),
            ]

            @staticmethod
            def request(method, url, headers=None, json=None, timeout=30):
                SeqReq.calls += 1
                return SeqReq.seq[min(SeqReq.calls - 1, len(SeqReq.seq) - 1)]

        monkeypatch.setattr(runpod, 'requests', SeqReq)
        out = runpod.rest_request('GET', '/retry')
        assert out == {'ok': True}
        assert SeqReq.calls == 3

    def test_rest_request_network_error_then_success(self, monkeypatch):

        class _SDK:
            api_key = 'k'

        monkeypatch.setattr('sky.adaptors.runpod.runpod', _SDK)

        class NetSeqReq:
            calls = 0

            @staticmethod
            def request(method, url, headers=None, json=None, timeout=30):
                NetSeqReq.calls += 1
                if NetSeqReq.calls < 2:
                    raise RuntimeError('net')
                return TestRunPodProvisionVolume._Resp(status_code=200,
                                                       text='{"v":1}',
                                                       json_obj={'v': 1})

        monkeypatch.setattr(runpod, 'requests', NetSeqReq)
        out = runpod.rest_request('GET', '/net')
        assert out == {'v': 1}
        assert NetSeqReq.calls == 2

    def test_rest_request_network_error_exhaustion(self, monkeypatch):

        class _SDK:
            api_key = 'k'

        monkeypatch.setattr('sky.adaptors.runpod.runpod', _SDK)

        class AlwaysNetErr:
            calls = 0

            @staticmethod
            def request(method, url, headers=None, json=None, timeout=30):
                AlwaysNetErr.calls += 1
                raise RuntimeError('net')

        monkeypatch.setattr(runpod, 'requests', AlwaysNetErr)
        with pytest.raises(RuntimeError):
            _ = runpod.rest_request('GET', '/net-exhaust')
        assert AlwaysNetErr.calls == runpod._MAX_RETRIES

    def test_rest_request_retry_exhaustion_5xx(self, monkeypatch):

        class _SDK:
            api_key = 'k'

        monkeypatch.setattr('sky.adaptors.runpod.runpod', _SDK)

        class Always500:
            calls = 0

            @staticmethod
            def request(method, url, headers=None, json=None, timeout=30):
                Always500.calls += 1
                return TestRunPodProvisionVolume._Resp(status_code=500,
                                                       text='boom')

        monkeypatch.setattr(runpod, 'requests', Always500)
        with pytest.raises(RuntimeError):
            _ = runpod.rest_request('GET', '/exhaust')
        assert Always500.calls == runpod._MAX_RETRIES

    def test_rest_request_non_retryable_4xx_single_attempt(self, monkeypatch):

        class _SDK:
            api_key = 'k'

        monkeypatch.setattr('sky.adaptors.runpod.runpod', _SDK)

        class Always400:
            calls = 0

            @staticmethod
            def request(method, url, headers=None, json=None, timeout=30):
                Always400.calls += 1
                return TestRunPodProvisionVolume._Resp(status_code=400,
                                                       text='bad')

        monkeypatch.setattr(runpod, 'requests', Always400)
        with pytest.raises(RuntimeError):
            _ = runpod.rest_request('GET', '/bad')
        assert Always400.calls == 1

    def test_list_volumes_variants(self, monkeypatch):

        class _SDK:
            api_key = 'k'

        monkeypatch.setattr('sky.adaptors.runpod.runpod', _SDK)
        # direct list
        self._mock_requests(monkeypatch, self._Resp(200,
                                                    text='[ ]',
                                                    json_obj=[]))
        assert runpod_prov._list_volumes() == []
        # dict wrappers
        self._mock_requests(
            monkeypatch,
            self._Resp(200,
                       json_obj={'items': [{
                           'id': '1'
                       }]},
                       text='{"items":[{"id":"1"}]}'))
        assert runpod_prov._list_volumes() == [{'id': '1'}]
        self._mock_requests(
            monkeypatch,
            self._Resp(200,
                       json_obj={'data': [{
                           'id': '2'
                       }]},
                       text='{"data":[{"id":"2"}]}'))
        assert runpod_prov._list_volumes() == [{'id': '2'}]
        self._mock_requests(
            monkeypatch,
            self._Resp(200,
                       json_obj={'networkVolumes': [{
                           'id': '3'
                       }]},
                       text='{"networkVolumes":[{"id":"3"}]}'))
        assert runpod_prov._list_volumes() == [{'id': '3'}]
        # unknown shape
        self._mock_requests(
            monkeypatch, self._Resp(200, json_obj={'foo': 1}, text='{"foo":1}'))
        assert runpod_prov._list_volumes() == []

    def test_try_resolve_volume_id(self, monkeypatch):
        monkeypatch.setattr(
            runpod_prov, '_list_volumes', lambda: [{
                'name': 'n1',
                'id': 'i1',
                'dataCenterId': 'iad-1'
            }])
        assert runpod_prov._try_resolve_volume_id('n1', 'iad-1') == 'i1'
        assert runpod_prov._try_resolve_volume_id('n2', 'iad-1') is None
        assert runpod_prov._try_resolve_volume_id('n1', 'nyc-1') is None

    def test_try_resolve_volume_by_name(self, monkeypatch):
        monkeypatch.setattr(
            runpod_prov, '_list_volumes', lambda: [{
                'name': 'n1',
                'id': 'i1',
                'size': 100,
                'dataCenterId': 'iad-1'
            }])
        vol = runpod_prov._try_resolve_volume_by_name('n1', 'iad-1')
        assert vol is not None
        assert vol['id'] == 'i1'
        assert vol['size'] == 100
        assert runpod_prov._try_resolve_volume_by_name('n2', 'iad-1') is None
        assert runpod_prov._try_resolve_volume_by_name('n1', 'nyc-1') is None

    def test_apply_volume_reuse_existing(self, monkeypatch):

        class Cfg:
            name_on_cloud = 'vol'
            size = '100'
            zone = 'iad-1'
            id_on_cloud = None
            zone = 'iad-1'
            config = {}

        cfg = Cfg()
        monkeypatch.setattr(
            runpod_prov, '_try_resolve_volume_by_name',
            lambda name, data_center_id: {
                'id': 'VID',
                'size': 100
            })
        called = {'post': False}

        def _rest(method, path, json=None):
            called['post'] = True
            return {}

        monkeypatch.setattr(runpod, 'rest_request', _rest)
        out = runpod_prov.apply_volume(cfg)
        assert out.id_on_cloud == 'VID'
        assert called['post'] is False

    def test_apply_volume_create_success(self, monkeypatch):

        class Cfg:
            name_on_cloud = 'vol'
            size = '100'
            zone = 'iad-1'
            id_on_cloud = None
            config = {}

        cfg = Cfg()
        monkeypatch.setattr(runpod_prov, '_try_resolve_volume_by_name',
                            lambda name, data_center_id: None)
        created = {}

        def _rest(method, path, json=None):
            created['payload'] = (method, path, json)
            return {'id': 'VID'}

        monkeypatch.setattr(runpod, 'rest_request', _rest)
        out = runpod_prov.apply_volume(cfg)
        assert out.id_on_cloud == 'VID'
        assert created['payload'][0] == 'POST'
        assert created['payload'][1] == '/networkvolumes'
        assert created['payload'][2]['dataCenterId'] == 'iad-1'
        assert created['payload'][2]['size'] == 100

    def test_apply_volume_use_existing_not_found(self, monkeypatch):

        class Cfg:
            name_on_cloud = 'vol'
            size = '100'
            zone = 'iad-1'
            id_on_cloud = None
            config = {'use_existing': True}

        cfg = Cfg()
        monkeypatch.setattr(runpod_prov, '_try_resolve_volume_by_name',
                            lambda name, data_center_id: None)
        with pytest.raises(ValueError) as exc_info:
            runpod_prov.apply_volume(cfg)
        assert 'does not exist while use_existing is True' in str(
            exc_info.value)

    def test_apply_volume_existing_no_id(self, monkeypatch):

        class Cfg:
            name_on_cloud = 'vol'
            size = '100'
            zone = 'iad-1'
            id_on_cloud = None
            config = {}

        cfg = Cfg()
        # Return a volume dict without 'id' field
        monkeypatch.setattr(
            runpod_prov, '_try_resolve_volume_by_name',
            lambda name, data_center_id: {
                'name': 'vol',
                'size': 100
            })
        with pytest.raises(RuntimeError) as exc_info:
            runpod_prov.apply_volume(cfg)
        assert 'has no id returned' in str(exc_info.value)

    def test_apply_volume_existing_size_mismatch(self, monkeypatch):

        class Cfg:
            name_on_cloud = 'vol'
            size = '100'
            zone = 'iad-1'
            id_on_cloud = None
            config = {}

        cfg = Cfg()
        # Return existing volume with different size
        monkeypatch.setattr(
            runpod_prov, '_try_resolve_volume_by_name',
            lambda name, data_center_id: {
                'id': 'VID',
                'size': 200
            })

        # Capture warning logs
        import logging

        from sky import sky_logging
        warnings_logged = []
        original_warning = logging.Logger.warning

        def capture_warning(self, msg, *args, **kwargs):
            warnings_logged.append(msg)
            return original_warning(self, msg, *args, **kwargs)

        monkeypatch.setattr(logging.Logger, 'warning', capture_warning)

        out = runpod_prov.apply_volume(cfg)
        assert out.id_on_cloud == 'VID'
        assert out.size == '200'  # Should be overridden with existing volume's size
        assert any('size' in str(w) and 'overriding' in str(w).lower()
                   for w in warnings_logged)

    def test_apply_volume_existing_no_size(self, monkeypatch):

        class Cfg:
            name_on_cloud = 'vol'
            size = '100'
            zone = 'iad-1'
            id_on_cloud = None
            config = {}

        cfg = Cfg()
        # Return existing volume without size field
        monkeypatch.setattr(runpod_prov, '_try_resolve_volume_by_name',
                            lambda name, data_center_id: {'id': 'VID'})

        # Capture warning logs
        import logging
        warnings_logged = []
        original_warning = logging.Logger.warning

        def capture_warning(self, msg, *args, **kwargs):
            warnings_logged.append(msg)
            return original_warning(self, msg, *args, **kwargs)

        monkeypatch.setattr(logging.Logger, 'warning', capture_warning)

        out = runpod_prov.apply_volume(cfg)
        assert out.id_on_cloud == 'VID'
        assert out.size == '100'  # Should keep original config size
        assert any('no size returned' in str(w) for w in warnings_logged)

    def test_apply_volume_errors(self, monkeypatch):

        class Cfg:
            name_on_cloud = 'vol'
            size = None
            zone = 'iad-1'
            id_on_cloud = None
            config = {}

        cfg = Cfg()
        monkeypatch.setattr(runpod_prov, '_try_resolve_volume_by_name',
                            lambda name, data_center_id: None)
        with pytest.raises(RuntimeError) as exc_info:
            runpod_prov.apply_volume(cfg)
        assert 'RunPod network volume size must be specified to create' in str(
            exc_info.value)
        cfg.size = 'abc'
        with pytest.raises(RuntimeError) as exc_info:
            runpod_prov.apply_volume(cfg)
        assert 'Invalid volume size' in str(exc_info.value)
        from sky.utils import volume as utils_volume
        cfg.size = str(
            max(1, utils_volume.MIN_RUNPOD_NETWORK_VOLUME_SIZE_GB - 1))
        with pytest.raises(RuntimeError) as exc_info:
            runpod_prov.apply_volume(cfg)
        assert 'RunPod network volume size must be at least' in str(
            exc_info.value)
        cfg.size = '100'
        cfg.zone = None
        with pytest.raises(RuntimeError) as exc_info:
            runpod_prov.apply_volume(cfg)
        assert 'RunPod DataCenterId is required for network volumes' in str(
            exc_info.value)
        cfg = Cfg()
        cfg.size = '100'
        created = {}

        def _rest(method, path, json=None):
            created['payload'] = (method, path, json)
            return "text"

        monkeypatch.setattr(runpod, 'rest_request', _rest)
        with pytest.raises(RuntimeError) as exc_info:
            runpod_prov.apply_volume(cfg)
        assert 'Failed to create RunPod network volume' in str(exc_info.value)

    def test_delete_volume_paths(self, monkeypatch):

        deleted = {'path': None}

        def _rest(method, path, json=None):
            deleted['path'] = (method, path)
            return None

        monkeypatch.setattr(runpod, 'rest_request', _rest)

        # id known
        class Cfg:
            name_on_cloud = 'vol'
            id_on_cloud = 'VID'
            zone = 'iad-1'
            config = {}

        cfg = Cfg()
        runpod_prov.delete_volume(cfg)
        assert deleted['path'] == ('DELETE', '/networkvolumes/VID')
        # resolve by name
        deleted['path'] = None
        cfg2 = Cfg()
        cfg2.id_on_cloud = None
        monkeypatch.setattr(runpod_prov, '_try_resolve_volume_id',
                            lambda name, data_center_id: 'VID2')
        runpod_prov.delete_volume(cfg2)
        assert deleted['path'] == ('DELETE', '/networkvolumes/VID2')
        # not found
        deleted['path'] = None
        monkeypatch.setattr(runpod_prov, '_try_resolve_volume_id',
                            lambda name, data_center_id: None)
        runpod_prov.delete_volume(cfg2)
        assert deleted['path'] is None

    def test_get_volume_usedby_no_id(self, monkeypatch):

        class Cfg:
            id_on_cloud = None
            name_on_cloud = 'vol'
            zone = 'iad-1'
            config = {}

        monkeypatch.setattr(runpod_prov, '_try_resolve_volume_id',
                            lambda name, data_center_id: None)
        used_pods, used_clusters = runpod_prov.get_volume_usedby(Cfg())
        assert used_pods == [] and used_clusters == []

    def test_get_volume_usedby_with_pods(self, monkeypatch):

        class Cfg:
            id_on_cloud = 'VID'
            name_on_cloud = 'vol'
            zone = 'iad-1'
            config = {}

        # Mock GraphQL response
        class _API:

            class api:

                class graphql:

                    @staticmethod
                    def run_graphql_query(query):
                        return {
                            'data': {
                                'myself': {
                                    'pods': [{
                                        'id': 'p1',
                                        'name': 'cluster-a-user-hash-head',
                                        'networkVolumeId': 'VID'
                                    }, {
                                        'id': 'p2',
                                        'name': 'other',
                                        'networkVolumeId': 'X'
                                    }]
                                }
                            }
                        }

        monkeypatch.setattr('sky.adaptors.runpod.runpod', _API)
        # Mock clusters
        monkeypatch.setattr(
            'sky.global_user_state.get_clusters', lambda: [{
                'name': 'cluster-a'
            }, {
                'name': 'cluster-a-b'
            }])
        monkeypatch.setattr('sky.utils.common_utils.get_user_hash',
                            lambda: 'user-hash')
        used_pods, used_clusters = runpod_prov.get_volume_usedby(Cfg())
        assert used_pods == ['cluster-a-user-hash-head']
        assert used_clusters == ['cluster-a']

    def test_get_all_volumes_usedby_with_pods(self, monkeypatch):

        class Cfg:
            id_on_cloud = 'VID'
            name_on_cloud = 'vol'
            zone = 'iad-1'
            config = {}

        # Mock GraphQL response
        class _API:

            class api:

                class graphql:

                    @staticmethod
                    def run_graphql_query(query):
                        return {
                            'data': {
                                'myself': {
                                    'pods': [{
                                        'id': 'p1',
                                        'name': 'cluster-a-user-hash-head',
                                        'networkVolumeId': 'VID'
                                    }, {
                                        'id': 'p2',
                                        'name': 'other',
                                        'networkVolumeId': 'X'
                                    }]
                                }
                            }
                        }

        monkeypatch.setattr('sky.adaptors.runpod.runpod', _API)
        # Mock clusters
        monkeypatch.setattr(
            'sky.global_user_state.get_clusters', lambda: [{
                'name': 'cluster-a'
            }, {
                'name': 'cluster-a-b'
            }])
        monkeypatch.setattr('sky.utils.common_utils.get_user_hash',
                            lambda: 'user-hash')
        config = Cfg()
        used_pods, used_clusters = runpod_prov.get_all_volumes_usedby([config])
        used_pods, used_clusters = runpod_prov.map_all_volumes_usedby(
            used_pods, used_clusters, config)
        assert used_pods == ['cluster-a-user-hash-head']
        assert used_clusters == ['cluster-a']

    def test_get_volume_usedby_resolve_id_missing_graphql_keys(
            self, monkeypatch):

        class Cfg:
            id_on_cloud = None
            name_on_cloud = 'vol'
            zone = 'iad-1'
            config = {}

        monkeypatch.setattr(runpod_prov, '_try_resolve_volume_id',
                            lambda name, data_center_id: 'VIDX')

        class _API:

            class api:

                class graphql:

                    @staticmethod
                    def run_graphql_query(query):
                        return {}  # missing keys -> defaults to []

        monkeypatch.setattr('sky.adaptors.runpod.runpod', _API)
        used_pods, used_clusters = runpod_prov.get_volume_usedby(Cfg())
        assert used_pods == [] and used_clusters == []

    def test_get_volume_usedby_filters_and_dedups(self, monkeypatch):

        class Cfg:
            id_on_cloud = 'VID'
            name_on_cloud = 'vol'
            zone = 'iad-1'
            config = {}

        class _API:

            class api:

                class graphql:

                    @staticmethod
                    def run_graphql_query(query):
                        return {
                            'data': {
                                'myself': {
                                    'pods': [
                                        {
                                            'id': 'p1',
                                            'name': None,
                                            'networkVolumeId': 'VID'
                                        },
                                        {
                                            'id': 'p2',
                                            'name': '',
                                            'networkVolumeId': 'VID'
                                        },
                                        {
                                            'id': 'p3',
                                            'name': 'cluster-a-user-hash-worker',
                                            'networkVolumeId': 'VID'
                                        },
                                        {
                                            'id': 'p4',
                                            'name': 'cluster-a-b-user-hash-head',
                                            'networkVolumeId': 'VID'
                                        },  # equality match
                                        {
                                            'id': 'p5',
                                            'name': 'cluster-a-user-hash-head',
                                            'networkVolumeId': 'VID'
                                        },
                                        {
                                            'id': 'p6',
                                            'name': 'other',
                                            'networkVolumeId': 'X'
                                        }
                                    ]
                                }
                            }
                        }

        monkeypatch.setattr('sky.adaptors.runpod.runpod', _API)
        monkeypatch.setattr(
            'sky.global_user_state.get_clusters', lambda: [{
                'name': 'cluster-a'
            }, {
                'name': ''
            }, {
                'name': None
            }, {
                'name': 'cluster-a-b'
            }])
        monkeypatch.setattr('sky.utils.common_utils.get_user_hash',
                            lambda: 'user-hash')
        used_pods, used_clusters = runpod_prov.get_volume_usedby(Cfg())
        assert used_pods == [
            'cluster-a-user-hash-worker', 'cluster-a-b-user-hash-head',
            'cluster-a-user-hash-head'
        ]
        assert used_clusters == ['cluster-a', 'cluster-a-b']

    def test_get_volume_usedby_empty_clusters(self, monkeypatch):

        class Cfg:
            id_on_cloud = 'VID'
            name_on_cloud = 'vol'
            zone = 'iad-1'
            config = {}

        class _API:

            class api:

                class graphql:

                    @staticmethod
                    def run_graphql_query(query):
                        return {
                            'data': {
                                'myself': {
                                    'pods': [{
                                        'id': 'p1',
                                        'name': 'c1-head',
                                        'networkVolumeId': 'VID'
                                    },]
                                }
                            }
                        }

        monkeypatch.setattr('sky.adaptors.runpod.runpod', _API)
        monkeypatch.setattr('sky.global_user_state.get_clusters', lambda: [])
        used_pods, used_clusters = runpod_prov.get_volume_usedby(Cfg())
        assert used_pods == ['c1-head']
        assert used_clusters == []
