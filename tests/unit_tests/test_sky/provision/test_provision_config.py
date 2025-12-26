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
