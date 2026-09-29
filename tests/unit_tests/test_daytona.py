"""Tests for the Daytona cloud provider."""

from unittest import mock

import pytest

from sky import clouds
from sky.clouds import daytona
from sky.provision.daytona import instance as daytona_instance
from sky.provision.daytona import utils as daytona_utils
from sky.utils import status_lib


class TestDaytonaCredentials:
    """Credential resolution and checking."""

    def test_check_credentials_missing(self, monkeypatch, tmp_path):
        fake_path = tmp_path / 'api_key'
        monkeypatch.setattr(daytona_utils, 'CREDENTIALS_PATH', str(fake_path))
        monkeypatch.setattr(daytona.Daytona, 'get_credentials_path',
                            classmethod(lambda cls: str(fake_path)))
        monkeypatch.delenv('DAYTONA_API_KEY', raising=False)

        valid, msg = daytona.Daytona.check_credentials(
            clouds.CloudCapability.COMPUTE)

        assert not valid
        assert 'Daytona API key not found' in msg

    def test_check_credentials_from_env(self, monkeypatch, tmp_path):
        fake_path = tmp_path / 'api_key'
        monkeypatch.setattr(daytona.Daytona, 'get_credentials_path',
                            classmethod(lambda cls: str(fake_path)))
        monkeypatch.setenv('DAYTONA_API_KEY', 'dtn_test')

        valid, msg = daytona.Daytona.check_credentials(
            clouds.CloudCapability.COMPUTE)

        assert valid
        assert msg is None

    def test_check_credentials_from_file(self, monkeypatch, tmp_path):
        cred_path = tmp_path / 'api_key'
        cred_path.write_text('dtn_test')
        monkeypatch.setattr(daytona.Daytona, 'get_credentials_path',
                            classmethod(lambda cls: str(cred_path)))
        monkeypatch.delenv('DAYTONA_API_KEY', raising=False)

        valid, msg = daytona.Daytona.check_credentials(
            clouds.CloudCapability.COMPUTE)

        assert valid
        assert msg is None

    def test_get_api_key_prefers_env(self, monkeypatch, tmp_path):
        cred_path = tmp_path / 'api_key'
        cred_path.write_text('dtn_file')
        monkeypatch.setattr(daytona_utils, 'CREDENTIALS_PATH', str(cred_path))
        monkeypatch.setenv('DAYTONA_API_KEY', 'dtn_env')

        assert daytona_utils.get_api_key() == 'dtn_env'

    def test_get_api_key_missing_raises(self, monkeypatch, tmp_path):
        monkeypatch.setattr(daytona_utils, 'CREDENTIALS_PATH',
                            str(tmp_path / 'missing'))
        monkeypatch.delenv('DAYTONA_API_KEY', raising=False)

        with pytest.raises(daytona_utils.DaytonaError):
            daytona_utils.get_api_key()


class TestDaytonaStatusMapping:
    """Sandbox state to SkyPilot cluster status mapping."""

    @pytest.mark.parametrize('state,expected', [
        ('started', status_lib.ClusterStatus.UP),
        ('creating', status_lib.ClusterStatus.INIT),
        ('pulling_snapshot', status_lib.ClusterStatus.INIT),
        ('error', status_lib.ClusterStatus.INIT),
        ('stopped', status_lib.ClusterStatus.STOPPED),
        ('stopping', status_lib.ClusterStatus.STOPPED),
        ('archived', status_lib.ClusterStatus.STOPPED),
        ('destroyed', None),
        ('deleted', None),
        (None, None),
    ])
    def test_to_cluster_status(self, state, expected):
        assert daytona_utils.to_cluster_status(state) == expected


class TestDaytonaInstanceTypes:
    """Instance type parsing into sandbox create request bodies."""

    def test_gpu_instance_type(self):
        body = daytona_utils._instance_type_to_create_body(  # pylint: disable=protected-access
            '2x-H100', use_spot=False)
        assert body == {
            'gpu': 2,
            'gpuType': ['H100'],
            'cpu': 16,
            'memory': 200,
        }

    def test_gpu_instance_type_spot(self):
        body = daytona_utils._instance_type_to_create_body(  # pylint: disable=protected-access
            '1x-MI355X', use_spot=True)
        assert body['spot'] is True
        assert body['gpuType'] == ['MI355X']

    def test_gpu_instance_type_name_mapping(self):
        body = daytona_utils._instance_type_to_create_body(  # pylint: disable=protected-access
            '1x-RTXPRO6000',
            use_spot=False)
        assert body['gpuType'] == ['RTX-PRO-6000']

    def test_consumer_gpu_memory(self):
        body = daytona_utils._instance_type_to_create_body(  # pylint: disable=protected-access
            '4x-RTX4090', use_spot=False)
        assert body['cpu'] == 32
        assert body['memory'] == 200  # 50 GiB per consumer GPU.

    def test_cpu_instance_type(self):
        body = daytona_utils._instance_type_to_create_body(  # pylint: disable=protected-access
            'cpu-4x-16gb', use_spot=False)
        assert body == {'cpu': 4, 'memory': 16}

    def test_cpu_instance_type_spot_rejected(self):
        with pytest.raises(daytona_utils.DaytonaError):
            daytona_utils._instance_type_to_create_body(  # pylint: disable=protected-access
                'cpu-4x-16gb',
                use_spot=True)

    def test_unknown_gpu_rejected(self):
        with pytest.raises(daytona_utils.DaytonaError):
            daytona_utils._instance_type_to_create_body(  # pylint: disable=protected-access
                '1x-B200', use_spot=False)


class TestDaytonaCapacityCheck:
    """Fail-fast GPU capacity checking."""

    _CAPACITY = {
        'capacity': [{
            'gpuType': 'H100',
            'availableOnDemand': 3,
            'availableSpot': 0
        },]
    }

    @mock.patch.object(daytona_utils, '_request')
    @mock.patch.object(daytona_utils,
                       '_get_organization_id',
                       return_value='org-1')
    def test_sufficient_capacity_passes(self, mock_org, mock_request):
        mock_request.return_value = self._CAPACITY
        daytona_utils.check_gpu_capacity('H100', 2, use_spot=False)

    @mock.patch.object(daytona_utils, '_request')
    @mock.patch.object(daytona_utils,
                       '_get_organization_id',
                       return_value='org-1')
    def test_insufficient_capacity_raises(self, mock_org, mock_request):
        mock_request.return_value = self._CAPACITY
        with pytest.raises(daytona_utils.DaytonaError, match='Insufficient'):
            daytona_utils.check_gpu_capacity('H100', 4, use_spot=False)

    @mock.patch.object(daytona_utils, '_request')
    @mock.patch.object(daytona_utils,
                       '_get_organization_id',
                       return_value='org-1')
    def test_zero_spot_capacity_raises(self, mock_org, mock_request):
        mock_request.return_value = self._CAPACITY
        with pytest.raises(daytona_utils.DaytonaError, match='Insufficient'):
            daytona_utils.check_gpu_capacity('H100', 1, use_spot=True)

    @mock.patch.object(daytona_utils, '_request')
    @mock.patch.object(daytona_utils,
                       '_get_organization_id',
                       return_value='org-1')
    def test_absent_type_raises(self, mock_org, mock_request):
        mock_request.return_value = self._CAPACITY
        with pytest.raises(daytona_utils.DaytonaError, match='no capacity'):
            daytona_utils.check_gpu_capacity('B300', 1, use_spot=False)

    @mock.patch.object(daytona_utils,
                       '_get_organization_id',
                       side_effect=daytona_utils.DaytonaError('down'))
    def test_capacity_endpoint_failure_is_ignored(self, mock_org):
        # The create call remains the source of truth.
        daytona_utils.check_gpu_capacity('H100', 1, use_spot=False)


class TestDaytonaProvisioning:
    """Provisioner behavior with a mocked Daytona API."""

    def test_run_instances_rejects_multi_node(self):
        config = mock.MagicMock()
        config.count = 2
        with pytest.raises(daytona_utils.DaytonaError):
            daytona_instance.run_instances('earth', 'cluster', 'cluster-abcd',
                                           config)

    @mock.patch.object(daytona_utils, 'wait_for_sandbox_started')
    @mock.patch.object(daytona_utils, 'launch_sandbox', return_value='sbx-1')
    @mock.patch.object(daytona_utils, 'list_cluster_sandboxes', return_value=[])
    def test_run_instances_creates_sandbox(self, mock_list, mock_launch,
                                           mock_wait):
        config = mock.MagicMock()
        config.count = 1
        config.node_config = {
            'InstanceType': '1x-H100',
            'DiskSize': 256,
            'ImageId': '',
            'Preemptible': False,
        }
        record = daytona_instance.run_instances('earth', 'cluster',
                                                'cluster-abcd', config)
        assert record.head_instance_id == 'sbx-1'
        assert record.created_instance_ids == ['sbx-1']
        mock_launch.assert_called_once_with(
            cluster_name_on_cloud='cluster-abcd',
            instance_type='1x-H100',
            region='earth',
            use_spot=False,
            disk_size=256,
            image_id=None,
        )
        mock_wait.assert_called_once_with('sbx-1')

    @mock.patch.object(daytona_utils, 'wait_for_sandbox_started')
    @mock.patch.object(daytona_utils, 'list_cluster_sandboxes')
    def test_run_instances_reuses_existing(self, mock_list, mock_wait):
        mock_list.return_value = [{'id': 'sbx-1', 'state': 'started'}]
        config = mock.MagicMock()
        config.count = 1
        record = daytona_instance.run_instances('earth', 'cluster',
                                                'cluster-abcd', config)
        assert record.head_instance_id == 'sbx-1'
        assert not record.created_instance_ids

    @mock.patch.object(daytona_utils,
                       'create_ssh_token',
                       return_value='token-123')
    @mock.patch.object(daytona_utils, 'list_cluster_sandboxes')
    def test_get_cluster_info_uses_token_as_ssh_user(self, mock_list,
                                                     mock_token):
        mock_list.return_value = [{'id': 'sbx-1', 'state': 'started'}]
        info = daytona_instance.get_cluster_info('earth', 'cluster-abcd')
        assert info.head_instance_id == 'sbx-1'
        assert info.ssh_user == 'token-123'
        instance = info.instances['sbx-1'][0]
        assert instance.external_ip == daytona_utils.SSH_GATEWAY_HOST
        assert instance.ssh_port == daytona_utils.SSH_GATEWAY_PORT

    @mock.patch.object(daytona_utils, 'delete_sandbox')
    @mock.patch.object(daytona_utils, 'list_cluster_sandboxes')
    def test_terminate_instances(self, mock_list, mock_delete):
        mock_list.return_value = [
            {
                'id': 'sbx-1',
                'state': 'started'
            },
            {
                'id': 'sbx-2',
                'state': 'error'
            },
        ]
        daytona_instance.terminate_instances('cluster-abcd')
        assert mock_delete.call_count == 2

    def test_stop_instances_unsupported(self):
        with pytest.raises(NotImplementedError):
            daytona_instance.stop_instances('cluster-abcd')

    def test_open_ports_unsupported(self):
        with pytest.raises(NotImplementedError):
            daytona_instance.open_ports('cluster-abcd', ['8080'])

    @mock.patch.object(daytona_utils, 'list_cluster_sandboxes')
    def test_query_instances(self, mock_list):
        mock_list.return_value = [
            {
                'id': 'sbx-1',
                'state': 'started',
                'errorReason': None
            },
            {
                'id': 'sbx-2',
                'state': 'destroyed',
                'errorReason': None
            },
        ]
        statuses = daytona_instance.query_instances('cluster', 'cluster-abcd')
        assert statuses == {'sbx-1': (status_lib.ClusterStatus.UP, None)}


class TestDaytonaCloudFeatures:
    """Cloud feature declarations."""

    def test_unsupported_features(self):
        features = daytona.Daytona._CLOUD_UNSUPPORTED_FEATURES  # pylint: disable=protected-access
        assert clouds.CloudImplementationFeatures.STOP in features
        assert clouds.CloudImplementationFeatures.MULTI_NODE in features
        assert clouds.CloudImplementationFeatures.OPEN_PORTS in features
        # Custom docker images are supported.
        assert (clouds.CloudImplementationFeatures.IMAGE_ID not in features)
        # Spot GPU sandboxes are supported.
        assert (clouds.CloudImplementationFeatures.SPOT_INSTANCE
                not in features)

    def test_repr(self):
        assert repr(daytona.Daytona()) == 'Daytona'
