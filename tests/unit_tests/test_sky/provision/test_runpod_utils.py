"""Unit tests for sky.provision.runpod.utils."""
from unittest import mock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from sky.provision.runpod import utils as runpod_utils
from sky.utils import resources_utils


def _launch_runpod(network_tier: resources_utils.NetworkTier,
                   preemptible: bool = False) -> str:
    return runpod_utils.launch(
        cluster_name='test-cluster',
        node_type='head',
        instance_type='1x_A100-80GB_SECURE',
        region='US',
        zone='US-CA-2',
        disk_size=50,
        image_name='runpod/base:1.0.2-ubuntu2204',
        ports=None,
        public_key='ssh-rsa test',
        preemptible=preemptible,
        bid_per_gpu=1.0,
        docker_login_config=None,
        network_tier=network_tier,
    )


@pytest.mark.parametrize('preemptible', [False, True])
def test_launch_best_network_tier_passes_bandwidth_requirements(preemptible):
    with patch('sky.provision.runpod.utils.runpod') as mock_runpod, patch(
            'sky.provision.runpod.utils.runpod_commands.create_spot_pod',
            return_value={'id': 'pod-id'}) as create_spot_pod, patch(
                'sky.provision.runpod.utils._rest_launchable_data_center_ids',
                return_value={'US-CA-2'}), patch(
                    'sky.provision.runpod.utils._available_data_center_ids',
                    return_value={'US-CA-2'}), patch(
                        'sky.provision.runpod.utils._create_pod_via_rest',
                        return_value={'id': 'pod-id'}) as create_rest_pod:
        mock_runpod.get_sdk_version_error.return_value = None
        mock_runpod.runpod.get_gpu.return_value = {'memoryInGb': 80}
        mock_runpod.runpod.create_pod.return_value = {'id': 'pod-id'}

        assert (_launch_runpod(resources_utils.NetworkTier.BEST,
                               preemptible=preemptible) == 'pod-id')

    if preemptible:
        create_kwargs = create_spot_pod.call_args.kwargs
        assert create_kwargs['min_download'] == 1000
        assert create_kwargs['min_upload'] == 1000
    else:
        create_params = create_rest_pod.call_args.args[0]
        assert create_params['minDownloadMbps'] == 1000
        assert create_params['minUploadMbps'] == 1000


def test_launch_standard_network_tier_omits_bandwidth_requirements():
    with patch('sky.provision.runpod.utils.runpod') as mock_runpod, patch(
            'sky.provision.runpod.utils._rest_launchable_data_center_ids',
            return_value={'US-CA-2'}), patch(
                'sky.provision.runpod.utils._available_data_center_ids',
                return_value={'US-CA-2'}), patch(
                    'sky.provision.runpod.utils._create_pod_via_rest',
                    return_value={'id': 'pod-id'}) as create_rest_pod:
        mock_runpod.get_sdk_version_error.return_value = None
        mock_runpod.runpod.get_gpu.return_value = {'memoryInGb': 80}
        mock_runpod.runpod.create_pod.return_value = {'id': 'pod-id'}

        _launch_runpod(resources_utils.NetworkTier.STANDARD)

    create_params = create_rest_pod.call_args.args[0]
    assert 'minDownloadMbps' not in create_params
    assert 'minUploadMbps' not in create_params


def test_launch_rejects_unsupported_sdk_version():
    with patch('sky.provision.runpod.utils.runpod') as mock_runpod:
        mock_runpod.get_sdk_version_error.return_value = (
            'RunPod SDK 1.7.9 is too old. Install "runpod>=1.7.10".')

        with pytest.raises(RuntimeError, match='runpod>=1.7.10'):
            _launch_runpod(resources_utils.NetworkTier.BEST)


class TestCreateTemplateForDockerLogin:

    def test_no_docker_login_config_returns_image_unchanged(self):
        image, template_id = runpod_utils._create_template_for_docker_login(
            cluster_name='test-cluster',
            image_name='my-org/my-image:tag',
            docker_login_config=None,
        )
        assert image == 'my-org/my-image:tag'
        assert template_id is None

    def test_docker_login_config_passes_formatted_image_to_create_template(
            self):
        """Regression test for #9546.

        create_template must receive the fully-qualified image name, not None.
        Passing None caused Python to serialize it as the literal string "None"
        in the GraphQL mutation (imageName: "None"), which the RunPod API now
        rejects as an invalid image name.
        """
        mock_auth_resp = {'id': 'auth-id-123'}
        mock_template_resp = {'id': 'template-id-456'}

        with patch('sky.provision.runpod.utils.runpod') as mock_runpod:
            mock_runpod.runpod.create_container_registry_auth.return_value = (
                mock_auth_resp)
            mock_runpod.runpod.create_template.return_value = mock_template_resp

            image, template_id = (
                runpod_utils._create_template_for_docker_login(
                    cluster_name='test-cluster',
                    image_name='my-org/my-image:tag',
                    docker_login_config={
                        'username': 'user',
                        'password': 'pass',
                        'server': 'ghcr.io',
                    },
                ))

        assert image == 'ghcr.io/my-org/my-image:tag'
        assert template_id == 'template-id-456'

        # The critical assertion: create_template must not receive None or
        # the string "None" as image_name.
        mock_runpod.runpod.create_template.assert_called_once_with(
            name=mock.ANY,
            image_name='ghcr.io/my-org/my-image:tag',
            registry_auth_id='auth-id-123',
        )

    def test_image_already_has_server_prefix_not_doubled(self):
        mock_auth_resp = {'id': 'auth-id-123'}
        mock_template_resp = {'id': 'template-id-456'}

        with patch('sky.provision.runpod.utils.runpod') as mock_runpod:
            mock_runpod.runpod.create_container_registry_auth.return_value = (
                mock_auth_resp)
            mock_runpod.runpod.create_template.return_value = mock_template_resp

            image, _ = runpod_utils._create_template_for_docker_login(
                cluster_name='test-cluster',
                image_name='ghcr.io/my-org/my-image:tag',
                docker_login_config={
                    'username': 'user',
                    'password': 'pass',
                    'server': 'ghcr.io',
                },
            )

        # Server prefix should not be doubled.
        assert image == 'ghcr.io/my-org/my-image:tag'
