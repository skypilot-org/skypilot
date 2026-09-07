"""Unit tests for sky.provision.common."""

import pytest

from sky.provision import common


class TestProvisionConfigRedaction:
    """Tests for ProvisionConfig redaction functionality."""

    def test_redact_docker_password(self):
        """Test that docker password is redacted from config."""
        config = common.ProvisionConfig(
            provider_config={},
            authentication_config={},
            docker_config={
                'docker_login_config': {
                    'username': 'testuser',
                    'password': 'secret-password-123',
                    'server': 'docker.io'
                }
            },
            node_config={},
            count=1,
            tags={},
            resume_stopped_nodes=False,
            ports_to_open_on_launch=None,
        )

        redacted = config.get_redacted_config()

        # Verify password is redacted
        assert redacted['docker_config']['docker_login_config'][
            'password'] == '<redacted>'

        # Verify other fields are preserved
        assert redacted['docker_config']['docker_login_config'][
            'username'] == 'testuser'
        assert redacted['docker_config']['docker_login_config'][
            'server'] == 'docker.io'
        assert redacted['count'] == 1
        assert redacted['resume_stopped_nodes'] is False

    def test_redact_without_docker_config(self):
        """Test redaction when docker_config doesn't contain sensitive fields."""
        config = common.ProvisionConfig(
            provider_config={},
            authentication_config={},
            docker_config={'image': 'ubuntu:latest'},
            node_config={},
            count=1,
            tags={},
            resume_stopped_nodes=False,
            ports_to_open_on_launch=None,
        )

        redacted = config.get_redacted_config()

        # Should not raise an error even if docker_login_config doesn't exist
        assert redacted['docker_config']['image'] == 'ubuntu:latest'
        # Should not create docker_login_config.password if it doesn't exist.
        assert 'docker_login_config' not in redacted['docker_config']

    def test_redact_provider_config_docker_password(self):
        """Test that a docker password under provider_config is redacted.

        Docker-native clouds (yotta, runpod) put the login in provider_config
        rather than docker_config, since docker_config is promised not to exist
        for clouds that hand out containers instead of VMs.
        """
        config = common.ProvisionConfig(
            provider_config={
                'docker_login_config': {
                    'username': 'testuser',
                    'password': 'secret-password-123',
                    'server': 'docker.io'
                }
            },
            authentication_config={},
            docker_config={},
            node_config={},
            count=1,
            tags={},
            resume_stopped_nodes=False,
            ports_to_open_on_launch=None,
        )

        redacted = config.get_redacted_config()

        # Verify password is redacted
        assert redacted['provider_config']['docker_login_config'][
            'password'] == '<redacted>'

        # Verify other fields are preserved
        assert redacted['provider_config']['docker_login_config'][
            'username'] == 'testuser'
        assert redacted['provider_config']['docker_login_config'][
            'server'] == 'docker.io'

    def test_redact_docker_password_in_both_locations(self):
        """Both docker_config and provider_config logins are redacted."""
        config = common.ProvisionConfig(
            provider_config={
                'docker_login_config': {
                    'username': 'provideruser',
                    'password': 'provider-secret',
                    'server': 'docker.io'
                }
            },
            authentication_config={},
            docker_config={
                'docker_login_config': {
                    'username': 'dockeruser',
                    'password': 'docker-secret',
                    'server': 'docker.io'
                }
            },
            node_config={},
            count=1,
            tags={},
            resume_stopped_nodes=False,
            ports_to_open_on_launch=None,
        )

        redacted = config.get_redacted_config()

        # Both locations are redacted, so adding one entry cannot silently
        # replace the other.
        assert redacted['docker_config']['docker_login_config'][
            'password'] == '<redacted>'
        assert redacted['provider_config']['docker_login_config'][
            'password'] == '<redacted>'

        # Verify other fields are preserved
        assert redacted['docker_config']['docker_login_config'][
            'username'] == 'dockeruser'
        assert redacted['provider_config']['docker_login_config'][
            'username'] == 'provideruser'

    def test_redact_without_provider_docker_config(self):
        """Test redaction when provider_config has no docker login."""
        config = common.ProvisionConfig(
            provider_config={'region': 'us-east-1'},
            authentication_config={},
            docker_config={},
            node_config={},
            count=1,
            tags={},
            resume_stopped_nodes=False,
            ports_to_open_on_launch=None,
        )

        redacted = config.get_redacted_config()

        # Should not raise an error even if docker_login_config doesn't exist
        assert redacted['provider_config']['region'] == 'us-east-1'
        # Should not create docker_login_config.password if it doesn't exist.
        assert 'docker_login_config' not in redacted['provider_config']
