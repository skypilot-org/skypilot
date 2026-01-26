"""Test Mithril volume."""
from unittest.mock import MagicMock

import pytest

from sky import models
from sky.provision.mithril import utils
from sky.provision.mithril import volume
from sky.volumes import volume as volume_lib


class TestMithrilVolume:

    def _mock_infra(self,
                    monkeypatch,
                    region='us-east-1',
                    zone=None,
                    cloud='mithril'):
        mock_infra_info = MagicMock()
        mock_infra_info.cloud = cloud
        mock_infra_info.region = region
        mock_infra_info.zone = zone
        monkeypatch.setattr('sky.utils.infra_utils.InfraInfo.from_str',
                            lambda x: mock_infra_info)

    @pytest.mark.parametrize('volume_type', [
        'mithril-file-share',
        'mithril-block',
    ])
    def test_factory_returns_mithril_subclass(self, monkeypatch, volume_type):
        self._mock_infra(monkeypatch, region='us-east-1')
        cfg = {
            'name': 'mv',
            'type': volume_type,
            'infra': 'mithril/us-east-1',
            'size': '100Gi',
        }
        vol = volume_lib.Volume.from_yaml_config(cfg)
        assert type(vol).__name__ in ('MithrilVolume',)
        assert vol.cloud == 'mithril'
        assert vol.region == 'us-east-1'
        assert vol.zone is None
        assert vol.size == '100'

    def test_validate_allows_missing_size(self, monkeypatch):
        self._mock_infra(monkeypatch, region='us-east-1')
        cfg = {
            'name': 'mv',
            'type': 'mithril-file-share',
            'infra': 'mithril/us-east-1',
        }
        vol = volume_lib.Volume.from_yaml_config(cfg)
        vol.validate(skip_cloud_compatibility=True)

    def test_labels_not_supported(self, monkeypatch):
        self._mock_infra(monkeypatch, region='us-east-1')
        cfg = {
            'name': 'mv',
            'type': 'mithril-file-share',
            'infra': 'mithril/us-east-1',
            'labels': {
                'key': 'value',
            },
        }
        vol = volume_lib.Volume.from_yaml_config(cfg)
        with pytest.raises(ValueError) as exc_info:
            vol.validate(skip_cloud_compatibility=True)
        assert 'Mithril volumes do not support labels' in str(exc_info.value)

    def test_zone_not_supported(self, monkeypatch):
        self._mock_infra(monkeypatch, region='us-east-1', zone='zone-1')
        cfg = {
            'name': 'mv',
            'type': 'mithril-file-share',
            'infra': 'mithril/us-east-1/zone-1',
        }
        vol = volume_lib.Volume.from_yaml_config(cfg)
        with pytest.raises(ValueError) as exc_info:
            vol.validate(skip_cloud_compatibility=True)
        assert 'Mithril volumes do not support zones' in str(exc_info.value)

    def test_region_required(self, monkeypatch):
        self._mock_infra(monkeypatch, region=None, zone=None)
        cfg = {
            'name': 'mv',
            'type': 'mithril-file-share',
            'infra': 'mithril',
        }
        vol = volume_lib.Volume.from_yaml_config(cfg)
        with pytest.raises(ValueError) as exc_info:
            vol.validate(skip_cloud_compatibility=True)
        assert 'Mithril region is required for volumes' in str(exc_info.value)

    def test_cloud_mismatch_raises(self, monkeypatch):
        self._mock_infra(monkeypatch, cloud='kubernetes')
        cfg = {
            'name': 'mv',
            'type': 'mithril-file-share',
            'infra': 'k8s',
        }
        with pytest.raises(ValueError) as exc_info:
            _ = volume_lib.Volume.from_yaml_config(cfg)
        assert 'Invalid cloud' in str(exc_info.value)


class TestMithrilProvisionVolume:
    """Tests for Mithril volume provisioning, mocking only at API boundary."""

    def _make_config(self,
                     name='vol',
                     size='100',
                     region='us-east-1',
                     vol_type='mithril-file-share',
                     id_on_cloud=None):
        return models.VolumeConfig(
            name=name,
            name_on_cloud=name,
            type=vol_type,
            cloud='mithril',
            size=size,
            region=region,
            zone=None,
            id_on_cloud=id_on_cloud,
            config={},
        )

    def _mock_api(self, monkeypatch, responses):
        """Mock utils.make_request and utils.get_config at the API boundary.

        Args:
            responses: dict mapping (method, path_prefix) to response or callable
        """

        def _make_request(method, path, payload=None, params=None):
            for (m, p), resp in responses.items():
                if method == m and path.startswith(p):
                    if callable(resp):
                        return resp(method, path, payload, params)
                    return resp
            raise ValueError(f'Unmocked API call: {method} {path}')

        monkeypatch.setattr(utils, 'get_config',
                            lambda: {'project_id': 'test-proj'})
        monkeypatch.setattr(utils, 'make_request', _make_request)

    def test_apply_volume_reuses_existing(self, monkeypatch):
        cfg = self._make_config()
        existing_vol = {'name': 'vol', 'fid': 'FID123', 'capacity_gb': 100}

        self._mock_api(monkeypatch, {
            ('GET', '/v2/volumes'): [existing_vol],
        })

        out = volume.apply_volume(cfg)
        assert out.id_on_cloud == 'FID123'
        assert out.size == '100'

    def test_apply_volume_creates_new(self, monkeypatch):
        cfg = self._make_config()
        created = {}

        def _handle_request(method, path, payload, params):
            if method == 'GET' and path == '/v2/volumes':
                return []  # no existing volume
            if method == 'POST' and path == '/v2/volumes':
                created['payload'] = payload
                return {'name': 'vol', 'fid': 'NEW_FID', 'capacity_gb': 100}
            raise ValueError(f'Unexpected: {method} {path}')

        self._mock_api(
            monkeypatch, {
                ('GET', '/v2/volumes'): lambda m, p, pl, pa: [],
                ('POST', '/v2/volumes'): lambda m, p, pl, pa:
                                         (created.update({'payload': pl}) or {
                                             'name': 'vol',
                                             'fid': 'NEW_FID',
                                             'capacity_gb': 100
                                         }),
            })

        out = volume.apply_volume(cfg)
        assert out.id_on_cloud == 'NEW_FID'
        assert created['payload']['size_gb'] == 100
        assert created['payload']['disk_interface'] == 'File'
        assert created['payload']['region'] == 'us-east-1'

    def test_apply_volume_size_mismatch_raises(self, monkeypatch):
        cfg = self._make_config(size='100')
        existing_vol = {'name': 'vol', 'fid': 'FID123', 'capacity_gb': 200}

        self._mock_api(monkeypatch, {
            ('GET', '/v2/volumes'): [existing_vol],
        })

        with pytest.raises(utils.MithrilError) as exc_info:
            volume.apply_volume(cfg)
        assert 'already exists with size 200' in str(exc_info.value)

    def test_apply_volume_missing_size_raises(self, monkeypatch):
        cfg = self._make_config(size=None)

        self._mock_api(monkeypatch, {
            ('GET', '/v2/volumes'): [],
        })

        with pytest.raises(utils.MithrilError) as exc_info:
            volume.apply_volume(cfg)
        assert 'size must be specified' in str(exc_info.value)

    def test_apply_volume_missing_region_raises(self, monkeypatch):
        cfg = self._make_config(region=None)

        with pytest.raises(utils.MithrilError) as exc_info:
            volume.apply_volume(cfg)
        assert 'region is required' in str(exc_info.value)

    def test_delete_volume_missing_fid_raises(self, monkeypatch):
        cfg = self._make_config(id_on_cloud=None)

        with pytest.raises(utils.MithrilError) as exc_info:
            volume.delete_volume(cfg)
        assert 'fid not found' in str(exc_info.value)

    def test_delete_volume_success(self, monkeypatch):
        cfg = self._make_config(id_on_cloud='FID123')
        deleted = {}

        self._mock_api(
            monkeypatch, {
                ('DELETE', '/v2/volumes/'): lambda m, p, pl, pa: (
                    deleted.update({'path': p}) or None),
            })

        volume.delete_volume(cfg)
        assert deleted['path'] == '/v2/volumes/FID123'

    def test_get_volume_usedby_not_found(self, monkeypatch):
        cfg = self._make_config()

        self._mock_api(monkeypatch, {
            ('GET', '/v2/volumes'): [],
        })

        used_instances, used_clusters = volume.get_volume_usedby(cfg)
        assert used_instances == []
        assert used_clusters == []

    def test_get_volume_usedby_with_bids(self, monkeypatch):
        cfg = self._make_config()
        vol = {
            'name': 'vol',
            'fid': 'FID',
            'capacity_gb': 100,
            'bids': ['bid1'],
            'reservations': []
        }

        def _handle_request(method, path, payload, params):
            if path == '/v2/volumes':
                return [vol]
            if path == '/v2/spot/bids':
                return {'data': [{'fid': 'bid1', 'name': 'cluster-a-bid'}]}
            if path == '/v2/reservation':
                return {'data': []}
            raise ValueError(f'Unexpected: {method} {path}')

        self._mock_api(
            monkeypatch, {
                ('GET', '/v2/volumes'): lambda m, p, pl, pa: [vol],
                ('GET', '/v2/spot/bids'): lambda m, p, pl, pa: {
                    'data': [{
                        'fid': 'bid1',
                        'name': 'cluster-a-bid'
                    }]
                },
                ('GET', '/v2/reservation'): lambda m, p, pl, pa: {
                    'data': []
                },
            })

        mock_handle = type('H', (),
                           {'cluster_name_on_cloud': 'cluster-a-bid'})()
        monkeypatch.setattr(
            'sky.global_user_state.get_clusters', lambda: [{
                'name': 'cluster-a',
                'handle': mock_handle
            }])

        used_instances, used_clusters = volume.get_volume_usedby(cfg)
        assert 'cluster-a-bid' in used_instances
        assert 'cluster-a' in used_clusters

    def test_get_all_volumes_usedby_exception_handling(self, monkeypatch):
        cfg_ok = self._make_config(name='vol-ok')
        cfg_fail = self._make_config(name='vol-fail')

        def _handle_request(method, path, payload=None, params=None):
            if path == '/v2/volumes':
                # Return volume that will cause KeyError (missing 'bids' key)
                return [
                    {
                        'name': 'vol-ok',
                        'fid': 'FID',
                        'capacity_gb': 100,
                        'bids': [],
                        'reservations': []
                    },
                    {
                        'name': 'vol-fail',
                        'fid': 'FID2',
                        'capacity_gb': 100
                    },
                    # missing 'bids' key causes KeyError
                ]
            return []

        monkeypatch.setattr(utils, 'get_config',
                            lambda: {'project_id': 'test-proj'})
        monkeypatch.setattr(utils, 'make_request', _handle_request)

        used_instances, used_clusters, failed = volume.get_all_volumes_usedby(
            [cfg_ok, cfg_fail])

        assert 'vol-ok' in used_instances
        assert 'vol-fail' in failed
