"""Tests for service account authentication client module."""

import asyncio
import os
import threading
import unittest.mock as mock

import pytest

from sky import exceptions
from sky.client import service_account_auth
from sky.server import common as server_common
from sky.server import constants as server_constants
from sky.skylet import constants


class TestServiceAccountAuth:
    """Test cases for service account authentication."""

    @mock.patch.dict(
        os.environ, {constants.SERVICE_ACCOUNT_TOKEN_ENV_VAR: 'sky_test_token'})
    def test_get_service_account_token_from_env(self):
        """Test getting service account token from environment variable."""
        token = service_account_auth._get_service_account_token()
        assert token == 'sky_test_token'

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch('sky.skypilot_config.get_nested')
    def test_get_service_account_token_from_config(self, mock_get_nested):
        """Test getting service account token from config file."""
        mock_get_nested.return_value = 'sky_config_token'

        token = service_account_auth._get_service_account_token()
        assert token == 'sky_config_token'

        # Verify the correct config path is used
        mock_get_nested.assert_called_once_with(
            ('api_server', 'service_account_token'), default_value=None)

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch('sky.skypilot_config.get_nested')
    def test_no_service_account_token(self, mock_get_nested):
        """Test no token returned when none available."""
        mock_get_nested.return_value = None

        token = service_account_auth._get_service_account_token()
        assert token is None

    @mock.patch.dict(os.environ,
                     {constants.SERVICE_ACCOUNT_TOKEN_ENV_VAR: 'invalid_token'})
    def test_invalid_token_format_env(self):
        """Test validation of token format from environment."""
        with pytest.raises(ValueError) as exc_info:
            service_account_auth._get_service_account_token()

        assert 'Invalid service account token format' in str(exc_info.value)
        assert 'Token must start with "sky_"' in str(exc_info.value)

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch('sky.skypilot_config.get_nested')
    def test_invalid_token_format_config(self, mock_get_nested):
        """Test validation of token format from config."""
        mock_get_nested.return_value = 'invalid_token'

        with pytest.raises(ValueError) as exc_info:
            service_account_auth._get_service_account_token()

        assert 'Invalid service account token format in config' in str(
            exc_info.value)
        assert 'Token must start with "sky_"' in str(exc_info.value)

    @mock.patch.dict(
        os.environ, {constants.SERVICE_ACCOUNT_TOKEN_ENV_VAR: 'sky_test_token'})
    def test_get_service_account_headers_with_token(self):
        """Test getting headers when token is available."""
        headers = service_account_auth.get_service_account_headers()
        assert headers == {'Authorization': 'Bearer sky_test_token'}

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch('sky.skypilot_config.get_nested')
    def test_get_service_account_headers_no_token(self, mock_get_nested):
        """Test getting headers when no token is available."""
        mock_get_nested.return_value = None

        headers = service_account_auth.get_service_account_headers()
        assert headers == {}

    @mock.patch.dict(
        os.environ, {constants.SERVICE_ACCOUNT_TOKEN_ENV_VAR: 'sky_test_token'})
    @mock.patch('sky.skypilot_config.get_nested')
    def test_env_variable_priority(self, mock_get_nested):
        """Test that environment variable takes priority over config."""
        mock_get_nested.return_value = 'sky_config_token'

        token = service_account_auth._get_service_account_token()
        # Should get env token, not config token
        assert token == 'sky_test_token'

        # Config should not be called when env var is present
        mock_get_nested.assert_not_called()

    @mock.patch.dict(
        os.environ,
        {constants.SERVICE_ACCOUNT_TOKEN_ENV_VAR: 'sky_valid_token'})
    def test_headers_with_valid_env_token(self):
        """Test headers generation with valid environment token."""
        headers = service_account_auth.get_service_account_headers()
        assert headers == {'Authorization': 'Bearer sky_valid_token'}

    @mock.patch.dict(os.environ,
                     {constants.SERVICE_ACCOUNT_TOKEN_ENV_VAR: 'invalid_token'})
    def test_headers_with_invalid_env_token(self):
        """Test headers generation fails with invalid environment token."""
        with pytest.raises(ValueError) as exc_info:
            service_account_auth.get_service_account_headers()

        assert 'Invalid service account token format' in str(exc_info.value)

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch('sky.skypilot_config.get_nested')
    def test_headers_with_invalid_config_token(self, mock_get_nested):
        """Test headers generation fails with invalid config token."""
        mock_get_nested.return_value = 'bad_token_format'

        with pytest.raises(ValueError) as exc_info:
            service_account_auth.get_service_account_headers()

        assert 'Invalid service account token format in config' in str(
            exc_info.value)

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch('sky.skypilot_config.get_nested')
    def test_empty_token_from_config(self, mock_get_nested):
        """Test empty/None token from config returns no headers."""
        mock_get_nested.return_value = None

        headers = service_account_auth.get_service_account_headers()
        assert headers == {}

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch('sky.skypilot_config.get_nested')
    def test_valid_config_token(self, mock_get_nested):
        """Test valid token from config works correctly."""
        mock_get_nested.return_value = 'sky_valid_config_token'

        headers = service_account_auth.get_service_account_headers()
        assert headers == {'Authorization': 'Bearer sky_valid_config_token'}

    @mock.patch.dict(os.environ, {constants.SERVICE_ACCOUNT_TOKEN_ENV_VAR: ''})
    def test_empty_env_token_falls_back_to_config(self):
        """Test empty environment token falls back to config."""
        with mock.patch('sky.skypilot_config.get_nested') as mock_get_nested:
            mock_get_nested.return_value = 'sky_config_fallback'

            token = service_account_auth._get_service_account_token()
            assert token == 'sky_config_fallback'

            # Should check config since env token is empty
            mock_get_nested.assert_called_once_with(
                ('api_server', 'service_account_token'), default_value=None)


_TOKEN_ENV = {constants.SERVICE_ACCOUNT_TOKEN_ENV_VAR: 'sky_admin'}


class TestImpersonation:
    """Test cases for acting as another user."""

    @mock.patch.dict(os.environ, _TOKEN_ENV, clear=True)
    def test_header_sent_only_inside_context(self):
        with service_account_auth.impersonate('alice'):
            headers = service_account_auth.get_service_account_headers()
        assert headers == {
            'Authorization': 'Bearer sky_admin',
            server_constants.IMPERSONATE_USER_HEADER: 'alice',
        }
        assert service_account_auth.get_service_account_headers() == {
            'Authorization': 'Bearer sky_admin'
        }

    @mock.patch.dict(os.environ, _TOKEN_ENV, clear=True)
    def test_nested_contexts_restore_outer_user(self):
        with service_account_auth.impersonate('alice'):
            with service_account_auth.impersonate('bob'):
                assert service_account_auth.get_impersonated_user() == 'bob'
            assert service_account_auth.get_impersonated_user() == 'alice'
        assert service_account_auth.get_impersonated_user() is None

    @mock.patch.dict(os.environ, {
        **_TOKEN_ENV, constants.IMPERSONATE_USER_ENV_VAR: 'carol'
    },
                     clear=True)
    def test_env_var_applies_and_context_takes_precedence(self):
        assert service_account_auth.get_impersonated_user() == 'carol'
        with service_account_auth.impersonate('alice'):
            assert service_account_auth.get_impersonated_user() == 'alice'

    @mock.patch.dict(os.environ, _TOKEN_ENV, clear=True)
    def test_context_does_not_leak_into_new_threads(self):
        seen = []
        with service_account_auth.impersonate('alice'):
            thread = threading.Thread(target=lambda: seen.append(
                service_account_auth.get_impersonated_user()))
            thread.start()
            thread.join()
        assert seen == [None]

    @mock.patch.dict(os.environ, _TOKEN_ENV, clear=True)
    def test_concurrent_tasks_act_as_different_users(self):

        async def headers_as(user):
            with service_account_auth.impersonate(user):
                await asyncio.sleep(0)
                return service_account_auth.get_service_account_headers()

        async def main():
            return await asyncio.gather(headers_as('alice'), headers_as('bob'))

        alice, bob = asyncio.run(main())
        assert alice[server_constants.IMPERSONATE_USER_HEADER] == 'alice'
        assert bob[server_constants.IMPERSONATE_USER_HEADER] == 'bob'

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch('sky.skypilot_config.get_nested', return_value=None)
    def test_requires_service_account_token(self, mock_get_nested):
        del mock_get_nested
        with service_account_auth.impersonate('alice'):
            with pytest.raises(ValueError, match='service account token'):
                service_account_auth.get_service_account_headers()

    @pytest.mark.parametrize('user', ['', '   '])
    def test_rejects_empty_user(self, user):
        with pytest.raises(ValueError, match='must not be empty'):
            with service_account_auth.impersonate(user):
                pass

    @pytest.mark.parametrize('remote_api_version,raises', [
        (None, False),
        (server_constants.MIN_IMPERSONATION_API_VERSION - 1, True),
        (server_constants.MIN_IMPERSONATION_API_VERSION, False),
    ])
    @mock.patch.dict(os.environ, _TOKEN_ENV, clear=True)
    @mock.patch('sky.server.common.rest.request')
    def test_refuses_server_that_ignores_header(self, mock_request,
                                                remote_api_version, raises):
        with mock.patch('sky.server.versions.get_remote_api_version',
                        return_value=remote_api_version), \
                service_account_auth.impersonate('alice'):
            if raises:
                with pytest.raises(exceptions.APINotSupportedError):
                    server_common.make_authenticated_request(
                        'POST', '/launch', server_url='https://api')
                mock_request.assert_not_called()
            else:
                server_common.make_authenticated_request(
                    'POST', '/launch', server_url='https://api')
                headers = mock_request.call_args.kwargs['headers']
                assert headers[
                    server_constants.IMPERSONATE_USER_HEADER] == 'alice'
