"""Tests for service account token functionality."""

import os
import types
import unittest.mock as mock

import fastapi
import pytest

from sky.client import service_account_auth
from sky.server.requests import payloads
from sky.skylet import constants
from sky.users import server as users_server


def _fake_request(user_id='admin-user'):
    """A minimal request whose auth_user the handler reads."""
    return types.SimpleNamespace(state=types.SimpleNamespace(
        auth_user=types.SimpleNamespace(id=user_id)))


class TestServiceAccountTokens:
    """Test cases for service account token operations."""

    @mock.patch.dict(
        os.environ, {constants.SERVICE_ACCOUNT_TOKEN_ENV_VAR: 'sky_test_token'})
    def test_token_authentication_headers(self):
        """Test service account token authentication via headers."""
        headers = service_account_auth.get_service_account_headers()
        assert headers == {
            'Authorization': 'Bearer sky_test_token',
        }

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch('sky.skypilot_config.get_nested')
    def test_no_token_no_headers(self, mock_get_nested):
        """Test no headers when no token is available."""
        mock_get_nested.return_value = None

        headers = service_account_auth.get_service_account_headers()
        assert headers == {}

    @mock.patch.dict(
        os.environ, {constants.SERVICE_ACCOUNT_TOKEN_ENV_VAR: 'sky_test_token'})
    def test_config_integration(self):
        """Test service account integration with configuration."""
        # Environment variable should take precedence
        token = service_account_auth._get_service_account_token()
        assert token == 'sky_test_token'

        # Headers should be properly formatted
        headers = service_account_auth.get_service_account_headers()
        assert 'Authorization' in headers
        assert headers['Authorization'].startswith('Bearer ')

    @mock.patch.dict(
        os.environ,
        {constants.SERVICE_ACCOUNT_TOKEN_ENV_VAR: 'sky_valid_token'})
    def test_headers_generation(self):
        """Test proper header generation with valid token."""
        headers = service_account_auth.get_service_account_headers()
        assert headers == {'Authorization': 'Bearer sky_valid_token'}

    @mock.patch.dict(os.environ,
                     {constants.SERVICE_ACCOUNT_TOKEN_ENV_VAR: 'invalid_token'})
    def test_invalid_token_validation(self):
        """Test that invalid tokens are properly rejected."""
        try:
            service_account_auth.get_service_account_headers()
            assert False, "Should have raised ValueError for invalid token"
        except ValueError as e:
            assert 'Invalid service account token format' in str(e)

    @mock.patch.dict(
        os.environ, {constants.SERVICE_ACCOUNT_TOKEN_ENV_VAR: 'sky_test_token'})
    def test_authentication_flow(self):
        """Test the complete authentication flow."""
        # Get token
        token = service_account_auth._get_service_account_token()
        assert token == 'sky_test_token'

        # Generate headers
        headers = service_account_auth.get_service_account_headers()
        assert headers['Authorization'] == 'Bearer sky_test_token'


class TestCreateServiceAccountTokenRole:
    """The create endpoint applies an optional role atomically."""

    def test_invalid_role_rejected(self):
        """An unknown role is rejected before anything is created."""
        body = payloads.ServiceAccountTokenCreateBody(
            token_name='scim_provisioning', expires_in_days=0, role='bogus')
        with pytest.raises(fastapi.HTTPException) as exc:
            users_server.create_service_account_token(_fake_request(), body)
        assert exc.value.status_code == 400
        assert 'Invalid role' in exc.value.detail

    @mock.patch('sky.users.server.permission')
    def test_non_admin_cannot_set_admin_role(self, mock_perm):
        """A non-admin caller can't create an admin-role token."""
        mock_perm.permission_service.get_user_roles.return_value = ['user']
        body = payloads.ServiceAccountTokenCreateBody(
            token_name='scim_provisioning', expires_in_days=0, role='admin')
        with pytest.raises(fastapi.HTTPException) as exc:
            users_server.create_service_account_token(
                _fake_request(user_id='regular-user'), body)
        assert exc.value.status_code == 403
        assert 'Only admins' in exc.value.detail

    @mock.patch('sky.users.server.global_user_state')
    @mock.patch('sky.users.server.token_service')
    @mock.patch('sky.users.server.permission')
    def test_non_admin_can_set_non_admin_role(self, mock_perm,
                                              mock_token_service, mock_gus):
        """A non-admin may create a token with a non-admin role."""
        mock_gus.add_or_update_user.return_value = True
        mock_perm.permission_service.get_user_roles.return_value = ['user']
        mock_token_service.token_service.create_token.return_value = {
            'token_id': 'tid',
            'token_hash': 'h',
            'token': 'sky_x',
            'expires_at': None,
        }
        body = payloads.ServiceAccountTokenCreateBody(token_name='sa_user',
                                                      expires_in_days=0,
                                                      role='user')
        result = users_server.create_service_account_token(
            _fake_request(user_id='regular-user'), body)
        assert result['token'] == 'sky_x'
        assert mock_perm.permission_service.update_role.call_args[0][
            1] == 'user'

    @mock.patch('sky.users.server.global_user_state')
    @mock.patch('sky.users.server.token_service')
    @mock.patch('sky.users.server.permission')
    def test_role_applied_at_create(self, mock_perm, mock_token_service,
                                    mock_gus):
        """An admin's requested role is applied in the same handler."""
        mock_gus.add_or_update_user.return_value = True  # new user
        mock_perm.permission_service.get_user_roles.return_value = ['admin']
        mock_token_service.token_service.create_token.return_value = {
            'token_id': 'tid',
            'token_hash': 'h',
            'token': 'sky_x',
            'expires_at': None,
        }
        body = payloads.ServiceAccountTokenCreateBody(
            token_name='scim_provisioning', expires_in_days=0, role='admin')
        result = users_server.create_service_account_token(
            _fake_request(), body)
        assert result['token'] == 'sky_x'
        mock_perm.permission_service.update_role.assert_called_once()
        assert mock_perm.permission_service.update_role.call_args[0][
            1] == 'admin'

    @mock.patch('sky.users.server.global_user_state')
    @mock.patch('sky.users.server.token_service')
    @mock.patch('sky.users.server.permission')
    def test_no_role_keeps_default_seed(self, mock_perm, mock_token_service,
                                        mock_gus):
        """Without a role, only the default seed runs (no update_role)."""
        mock_gus.add_or_update_user.return_value = True
        mock_token_service.token_service.create_token.return_value = {
            'token_id': 'tid',
            'token_hash': 'h',
            'token': 'sky_x',
            'expires_at': None,
        }
        body = payloads.ServiceAccountTokenCreateBody(token_name='sa_default',
                                                      expires_in_days=0)
        users_server.create_service_account_token(_fake_request(), body)
        mock_perm.seed_new_user_role.assert_called_once()
        mock_perm.permission_service.update_role.assert_not_called()
