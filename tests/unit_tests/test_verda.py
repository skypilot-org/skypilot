"""Tests for Verda Cloud provider."""

from pathlib import Path
import tempfile
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from sky import clouds
from sky.adaptors.verda import Instance
from sky.adaptors.verda import InstanceStatus
from sky.adaptors.verda import VerdaClient
from sky.clouds import verda
from sky.provision import common as provision_common
from sky.provision.verda import instance as verda_instance


def test_verda_cloud_basics():
    cloud = verda.Verda()
    assert cloud.name == "verda"
    assert cloud._REPR == "Verda"
    assert cloud._MAX_CLUSTER_NAME_LEN_LIMIT == 120


def test_verda_credential_file_mounts():
    with tempfile.TemporaryDirectory() as tmpdir:
        cred_path = Path(tmpdir) / "config.yaml"
        cred_path.touch()
        with pytest.MonkeyPatch.context() as m:
            m.setattr(verda.Verda, "CREDENTIALS_PATH", str(cred_path))
            cloud = verda.Verda()
            mounts = cloud.get_credential_file_mounts()
            assert str(cred_path) in mounts
            assert mounts[str(cred_path)] == "~/.verda/config.json"


def test_verda_region_zone_validation_disallows_zones():
    cloud = verda.Verda()
    with pytest.raises(ValueError, match="does not support zones"):
        cloud.validate_region_zone("some-region", "zone-1")


@patch('sky.clouds.verda.get_verda_configuration')
def test_verda_check_credentials_missing(mock_get_config, monkeypatch,
                                         tmp_path):
    cloud = verda.Verda()
    # Mock configuration to return False (not configured)
    mock_get_config.return_value = (False, "Verda credentials not found", None)

    fake_path = tmp_path / "config.json"
    monkeypatch.setattr(verda.Verda, "CREDENTIALS_PATH", str(fake_path))

    valid, msg = cloud.check_credentials(clouds.CloudCapability.COMPUTE)
    assert not valid
    assert "Verda credentials not found" in msg


class TestVerdaClientInstanceCreation:
    """Test cases for VerdaClient instance creation through Verda adaptor."""

    @patch('sky.adaptors.verda.get_verda_configuration')
    @patch('sky.adaptors.verda.requests.post')
    @patch('sky.adaptors.verda.requests.get')
    @patch('sky.adaptors.verda.time.time')
    def test_instance_create_success(self, mock_time, mock_get, mock_post,
                                     mock_get_config):
        """Test successful instance creation."""
        # Mock time to control token expiration
        mock_time.return_value = 1000.0

        # Mock configuration
        mock_config = MagicMock()
        mock_config.client_id = "test-client-id"
        mock_config.client_secret = "test-client-secret"
        mock_config.base_url = "https://api.verda.com/v1"
        mock_get_config.return_value = (True, None, mock_config)

        # Mock authentication response (called during HTTPClient.__init__)
        auth_response = MagicMock()
        auth_response.ok = True
        auth_response.json.return_value = {
            'access_token': 'test-access-token',
            'refresh_token': 'test-refresh-token',
            'scope': 'read write',
            'token_type': 'Bearer',
            'expires_in': 3600
        }
        auth_response.status_code = 200

        # Mock instance creation response
        create_response = MagicMock()
        create_response.ok = True
        create_response.text = "instance-123"
        create_response.status_code = 200

        # Mock instance get response
        get_response = MagicMock()
        get_response.ok = True
        get_response.json.return_value = {
            'id': 'instance-123',
            'status': InstanceStatus.RUNNING,
            'ip': '10.0.0.1',
            'hostname': 'test-cluster-head'
        }
        get_response.status_code = 200

        # Set up mock call order: auth (during HTTPClient init), create, get
        mock_post.side_effect = [auth_response, create_response]
        mock_get.return_value = get_response

        # Create client and instance
        client = VerdaClient()
        payload = {
            'instance_type': 'gpu-h100-8gpu',
            'hostname': 'test-cluster-head',
            'location_code': 'FIN-03',
            'is_spot': False,
            'contract': 'PAY_AS_YOU_GO',
            'image': 'ubuntu-24.04-cuda-12.8-open-docker',
            'description': 'Created by SkyPilot',
            'ssh_key_ids': ['ssh-key-1'],
            'os_volume': {
                'name': 'test-cluster-head',
                'size': 50
            }
        }

        instance = client.instance_create(payload)

        # Verify instance creation
        assert isinstance(instance, Instance)
        assert instance.instance_id == 'instance-123'
        assert instance.status == InstanceStatus.RUNNING
        assert instance.ip == '10.0.0.1'
        assert instance.hostname == 'test-cluster-head'

        # Verify API calls
        # First post is auth (during HTTPClient.__init__), second is instance create
        assert mock_post.call_count == 2
        assert mock_get.call_count == 1

        # Verify auth call
        auth_call = mock_post.call_args_list[0]
        assert 'https://api.verda.com/v1/oauth2/token' in auth_call[0][0]
        assert auth_call[1]['json']['grant_type'] == 'client_credentials'

        # Verify create call - check URL and payload
        create_call = mock_post.call_args_list[1]
        assert 'https://api.verda.com/v1/instances' in create_call[0][0]
        assert create_call[1]['json'] == payload

        # Verify get call
        get_call = mock_get.call_args
        assert 'https://api.verda.com/v1/instances/instance-123' in get_call[0][
            0]

    @patch('sky.adaptors.verda.get_verda_configuration')
    @patch('sky.adaptors.verda.requests.post')
    @patch('sky.adaptors.verda.requests.get')
    @patch('sky.adaptors.verda.time.time')
    def test_instance_create_with_spot(self, mock_time, mock_get, mock_post,
                                       mock_get_config):
        """Test instance creation with spot/preemptible instance."""
        # Mock time to control token expiration
        mock_time.return_value = 1000.0

        # Mock configuration
        mock_config = MagicMock()
        mock_config.client_id = 'test-client-id'
        mock_config.client_secret = 'test-client-secret'
        mock_config.base_url = 'https://api.verda.com/v1'
        mock_get_config.return_value = (True, None, mock_config)

        # Mock authentication response (called during HTTPClient.__init__)
        auth_response = MagicMock()
        auth_response.ok = True
        auth_response.json.return_value = {
            'access_token': 'test-access-token',
            'refresh_token': 'test-refresh-token',
            'scope': 'read write',
            'token_type': 'Bearer',
            'expires_in': 3600
        }
        auth_response.status_code = 200

        # Mock instance creation response
        create_response = MagicMock()
        create_response.ok = True
        create_response.text = "instance-456"
        create_response.status_code = 200

        # Mock instance get response
        get_response = MagicMock()
        get_response.ok = True
        get_response.json.return_value = {
            'id': 'instance-456',
            'status': InstanceStatus.PROVISIONING,
            'ip': None,
            'hostname': 'test-cluster-worker'
        }
        get_response.status_code = 200

        # Set up mock call order: auth (during HTTPClient init), create, get
        mock_post.side_effect = [auth_response, create_response]
        mock_get.return_value = get_response

        # Create client and instance with spot
        client = VerdaClient()
        payload = {
            'instance_type': 'gpu-h100-8gpu',
            'hostname': 'test-cluster-worker',
            'location_code': 'FIN-03',
            'is_spot': True,
            'contract': 'SPOT',
            'image': 'ubuntu-24.04-cuda-12.8-open-docker',
            'description': 'Created by SkyPilot',
            'ssh_key_ids': ['ssh-key-1'],
            'os_volume': {
                'name': 'test-cluster-worker',
                'size': 100
            }
        }

        instance = client.instance_create(payload)

        # Verify instance creation
        assert isinstance(instance, Instance)
        assert instance.instance_id == 'instance-456'
        assert instance.status == InstanceStatus.PROVISIONING
        assert instance.ip is None
        assert instance.hostname == 'test-cluster-worker'

        # Verify create call has spot settings
        create_call = mock_post.call_args_list[1]
        assert create_call[1]['json']['is_spot'] is True
        assert create_call[1]['json']['contract'] == 'SPOT'

    @patch('sky.adaptors.verda.get_verda_configuration')
    @patch('sky.adaptors.verda.requests.post')
    @patch('sky.adaptors.verda.time.time')
    def test_instance_create_api_error(self, mock_time, mock_post,
                                       mock_get_config):
        """Test instance creation with API error."""
        # Mock time to control token expiration
        mock_time.return_value = 1000.0

        # Mock configuration
        mock_config = MagicMock()
        mock_config.client_id = "test-client-id"
        mock_config.client_secret = "test-client-secret"
        mock_config.base_url = "https://api.verda.com/v1"
        mock_get_config.return_value = (True, None, mock_config)

        # Mock authentication response (called during HTTPClient.__init__)
        auth_response = MagicMock()
        auth_response.ok = True
        auth_response.json.return_value = {
            'access_token': 'test-access-token',
            'refresh_token': 'test-refresh-token',
            'scope': 'read write',
            'token_type': 'Bearer',
            'expires_in': 3600
        }
        auth_response.status_code = 200

        # Mock instance creation error response
        error_response = MagicMock()
        error_response.ok = False
        error_response.status_code = 400
        error_response.text = '{"code": "INVALID_INPUT", "message": "Invalid instance type"}'

        # Set up mock call order: auth (during HTTPClient init), create (error)
        mock_post.side_effect = [auth_response, error_response]

        # Create client and attempt instance creation
        client = VerdaClient()
        payload = {
            'instance_type': 'invalid-instance-type',
            'hostname': 'test-cluster-head',
            'location_code': 'FIN-03',
            'is_spot': False,
            'contract': 'PAY_AS_YOU_GO',
            'image': 'ubuntu-24.04-cuda-12.8-open-docker',
            'description': 'Created by SkyPilot',
            'ssh_key_ids': ['ssh-key-1'],
            'os_volume': {
                'name': 'test-cluster-head',
                'size': 50
            }
        }

        # Verify APIException is raised
        from sky.adaptors.verda import VerdaException
        with pytest.raises(VerdaException) as exc_info:
            client.instance_create(payload)

        assert exc_info.value.code == 'INVALID_INPUT'
        assert 'Invalid instance type' in exc_info.value.message

    @patch('sky.adaptors.verda.get_verda_configuration')
    def test_instance_create_configuration_error(self, mock_get_config):
        """Test instance creation with configuration error."""
        # Mock configuration error
        mock_get_config.return_value = (False, "Configuration not found", None)

        # Verify RuntimeError is raised when creating VerdaClient
        # (HTTPClient is initialized lazily, but we can test by trying to use it)
        client = VerdaClient()
        # HTTPClient is created lazily when instance_create is called
        with pytest.raises(RuntimeError) as exc_info:
            client.instance_create({})

        assert "Can't connect to Verda Cloud" in str(exc_info.value)


class TestRunInstancesOsVolume:
    """`os_volume` must only be sent when `image` is an OS image type."""

    def _run(self, monkeypatch, image_id: str) -> dict:
        created = []

        def instances_get():
            if not created:
                return []
            inst = MagicMock()
            inst.instance_id = 'inst-1'
            inst.hostname = 'sky-abc-head'
            inst.status = InstanceStatus.RUNNING
            return [inst]

        def instance_create(payload):
            created.append(payload)
            response = MagicMock()
            response.instance_id = 'inst-1'
            return response

        ssh_key = MagicMock()
        ssh_key.public_key = 'ssh-ed25519 AAAA test'
        ssh_key.id = 'key-1'
        client = MagicMock()
        client.instances_get.side_effect = instances_get
        client.instance_create.side_effect = instance_create
        client.ssh_keys_get.return_value = [ssh_key]
        monkeypatch.setattr(verda_instance, 'verda', client)
        monkeypatch.setattr(verda_instance.time, 'sleep', lambda _: None)

        config = provision_common.ProvisionConfig(
            provider_config={'zones': None},
            authentication_config={},
            docker_config={},
            node_config={
                'InstanceType': '1A100.22V',
                'DiskSize': 100,
                'ImageId': image_id,
                'PublicKey': 'ssh-ed25519 AAAA test',
            },
            count=1,
            tags={},
            resume_stopped_nodes=False,
            ports_to_open_on_launch=None,
        )
        record = verda_instance.run_instances('FIN-01', 'sky-abc', 'sky-abc',
                                              config)
        assert record.created_instance_ids == ['inst-1']
        assert len(created) == 1
        return created[0]

    def test_image_type_sends_os_volume(self, monkeypatch):
        payload = self._run(monkeypatch, 'ubuntu-24.04-cuda-12.8-open-docker')
        assert payload['image'] == 'ubuntu-24.04-cuda-12.8-open-docker'
        assert payload['os_volume'] == {'name': 'sky-abc-head', 'size': 100}

    def test_os_volume_id_omits_os_volume(self, monkeypatch):
        volume_id = '0b1e7c3a-5d2f-4e8a-9c6b-1f2a3b4c5d6e'
        payload = self._run(monkeypatch, volume_id)
        assert payload['image'] == volume_id
        assert 'os_volume' not in payload
