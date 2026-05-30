"""Unit tests for sky.provision.runpod.utils."""
from unittest import mock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from sky.provision.runpod import utils as runpod_utils


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
