import os
import tempfile
from unittest import mock

import jwt as pyjwt
import pytest

from sky.server import config
from sky.server import daemons


@mock.patch('sky.utils.common_utils.get_mem_size_gb', return_value=8)
@mock.patch('sky.utils.common_utils.get_cpu_count', return_value=4)
def test_compute_server_config_on_minimal_deployment(cpu_count, mem_size_gb):
    # Test deployment mode
    c = config.compute_server_config(deploy=True)
    assert c.num_server_workers == 4
    assert c.long_worker_config.garanteed_parallelism == 8
    assert c.long_worker_config.burstable_parallelism == 0
    assert c.short_worker_config.garanteed_parallelism == 9
    assert c.short_worker_config.burstable_parallelism == 0
    assert c.queue_backend == config.QueueBackend.MULTIPROCESSING


@mock.patch('sky.utils.common_utils.get_mem_size_gb', return_value=784)
@mock.patch('sky.utils.common_utils.get_cpu_count', return_value=196)
def test_compute_server_config_on_large_deployment(cpu_count, mem_size_gb):
    # Test local mode with low resources
    c = config.compute_server_config(deploy=True)
    assert c.num_server_workers == 196
    assert c.long_worker_config.garanteed_parallelism == 392
    assert c.long_worker_config.burstable_parallelism == 0
    assert c.short_worker_config.garanteed_parallelism == 2084
    assert c.short_worker_config.burstable_parallelism == 0
    assert c.queue_backend == config.QueueBackend.MULTIPROCESSING


@mock.patch('sky.utils.common_utils.get_mem_size_gb', return_value=16)
@mock.patch('sky.utils.common_utils.get_cpu_count', return_value=4)
def test_compute_server_config(cpu_count, mem_size_gb):
    # Test deployment mode
    c = config.compute_server_config(deploy=True)
    assert c.num_server_workers == 4
    assert c.long_worker_config.garanteed_parallelism == 8
    assert c.long_worker_config.burstable_parallelism == 0
    assert c.short_worker_config.garanteed_parallelism == 36
    assert c.short_worker_config.burstable_parallelism == 0
    assert c.queue_backend == config.QueueBackend.MULTIPROCESSING

    # Test local mode with normal resources
    c = config.compute_server_config(deploy=False)
    assert c.num_server_workers == 1
    assert c.long_worker_config.garanteed_parallelism == 4
    assert c.long_worker_config.burstable_parallelism == 1024
    assert c.short_worker_config.garanteed_parallelism == 41
    assert c.short_worker_config.burstable_parallelism == 1024
    assert c.queue_backend == config.QueueBackend.LOCAL


@mock.patch('sky.utils.common_utils.get_mem_size_gb', return_value=1)
@mock.patch('sky.utils.common_utils.get_cpu_count', return_value=1)
def test_compute_server_config_low_resources(cpu_count, mem_size_gb):
    # Test local mode with low resources
    c = config.compute_server_config(deploy=False)
    assert c.num_server_workers == 1
    assert c.long_worker_config.garanteed_parallelism == 0
    assert c.long_worker_config.burstable_parallelism == 1024
    assert c.short_worker_config.garanteed_parallelism == 0
    assert c.short_worker_config.burstable_parallelism == 1024
    assert c.queue_backend == config.QueueBackend.LOCAL

    # Test deploymen mode with low resources
    c = config.compute_server_config(deploy=True)
    assert c.num_server_workers == 1
    assert c.long_worker_config.garanteed_parallelism == 1
    assert c.long_worker_config.burstable_parallelism == 0
    assert c.short_worker_config.garanteed_parallelism == 6
    assert c.short_worker_config.burstable_parallelism == 0
    assert c.queue_backend == config.QueueBackend.MULTIPROCESSING


@mock.patch('sky.utils.common_utils.get_mem_size_gb', return_value=48)
@mock.patch('sky.utils.common_utils.get_cpu_count', return_value=12)
@mock.patch('sky.utils.env_options.Options.RUNNING_IN_BUILDKITE.get',
            return_value=False)
def test_compute_server_config_pool(cpu_count, mem_size_gb, buildkite_mock):
    from sky.utils import controller_utils
    reserved_memory_mb = float(
        controller_utils.MAXIMUM_CONTROLLER_RESERVED_MEMORY_MB)

    # Test deployment mode with reserved memory
    c = config.compute_server_config(deploy=True,
                                     reserved_memory_mb=reserved_memory_mb)
    assert c.num_server_workers == 12
    assert c.long_worker_config.garanteed_parallelism == 24
    assert c.long_worker_config.burstable_parallelism == 0
    assert c.short_worker_config.garanteed_parallelism == 114
    assert c.short_worker_config.burstable_parallelism == 0
    assert c.queue_backend == config.QueueBackend.MULTIPROCESSING

    assert controller_utils._get_number_of_services(pool=True) == 5
    assert controller_utils._get_request_parallelism(pool=True) == 40


def _memory_aware_sizing():
    """Enable SKYPILOT_MEMORY_AWARE_WORKER_SIZING."""
    from sky.utils import env_options
    return mock.patch.dict(
        os.environ,
        {env_options.Options.MEMORY_AWARE_WORKER_SIZING.env_key: 'true'})


def _permanent_worker_memory_gb(c: 'config.ServerConfig') -> float:
    """Memory the permanent processes of a ServerConfig are budgeted to use."""
    # +1 for the parent process running the main event loop.
    return (
        (c.num_server_workers + 1) * config.SERVER_WORKER_MEM_GB +
        c.long_worker_config.garanteed_parallelism * config.LONG_WORKER_MEM_GB +
        c.short_worker_config.garanteed_parallelism *
        config.SHORT_WORKER_MEM_GB)


@mock.patch('sky.utils.common_utils.get_mem_size_gb', return_value=48)
@mock.patch('sky.utils.common_utils.get_cpu_count', return_value=12)
def test_memory_aware_sizing_reserves_server_workers(cpu_count, mem_size_gb):
    """Server worker memory comes out of the budget before the pools."""
    with _memory_aware_sizing():
        c = config.compute_server_config(deploy=True, quiet=True)
    # 13 resident server processes at 0.4GB = 5.2GB, of which the 2GB min-avail
    # reserve already covered part, so 3.2GB comes off the top.
    assert c.num_server_workers == 12
    assert c.long_worker_config.garanteed_parallelism == 24
    assert c.short_worker_config.garanteed_parallelism == 110
    assert _permanent_worker_memory_gb(c) <= 48


@mock.patch('sky.utils.common_utils.get_mem_size_gb', return_value=48)
@mock.patch('sky.utils.common_utils.get_cpu_count', return_value=12)
def test_memory_aware_sizing_consolidation_mode(cpu_count, mem_size_gb):
    """In consolidation mode the in-process controllers are reserved for."""
    from sky.utils import controller_utils

    with _memory_aware_sizing(), \
         mock.patch.object(controller_utils, 'is_jobs_consolidation_mode',
                           return_value=True), \
         mock.patch.object(controller_utils, '_is_consolidation_mode',
                           return_value=True), \
         mock.patch('sky.jobs.utils.is_consolidation_mode', return_value=True):
        reserved_memory_mb = (
            controller_utils.compute_memory_reserved_for_controllers(
                reserve_extra_for_pool=True))
        c = config.compute_server_config(deploy=True,
                                         quiet=True,
                                         reserved_memory_mb=reserved_memory_mb)

    # 2GB of headroom, doubled for the pool controller, plus 30% of the rest.
    assert reserved_memory_mb == pytest.approx(4096 + 0.3 * (48 * 1024 - 4096))
    assert c.num_server_workers == 12
    assert c.long_worker_config.garanteed_parallelism == 24
    assert c.short_worker_config.garanteed_parallelism == 53
    committed_gb = _permanent_worker_memory_gb(c) + reserved_memory_mb / 1024
    assert committed_gb <= 48


@mock.patch('sky.utils.common_utils.get_mem_size_gb', return_value=48)
@mock.patch('sky.utils.common_utils.get_cpu_count', return_value=12)
def test_memory_aware_sizing_off_by_default(cpu_count, mem_size_gb):
    """Without the env var, consolidation mode reserves nothing."""
    from sky.utils import controller_utils

    with mock.patch.object(controller_utils, 'is_jobs_consolidation_mode',
                           return_value=True), \
         mock.patch.object(controller_utils, '_is_consolidation_mode',
                           return_value=True):
        assert controller_utils.compute_memory_reserved_for_controllers(
            reserve_extra_for_pool=True) == 0.0


@pytest.mark.parametrize('gate_on', [False, True])
@mock.patch('sky.utils.common_utils.get_mem_size_gb', return_value=48)
@mock.patch('sky.utils.common_utils.get_cpu_count', return_value=12)
def test_controller_process_reserves_flat_headroom(cpu_count, mem_size_gb,
                                                   gate_on):
    """A local API server under a controller process keeps the flat headroom.

    _get_parallelism() sizes that machine assuming only the headroom was
    withheld, so the gate must not switch it to the scaling reservation.
    """
    from sky.skylet import constants as skylet_constants
    from sky.utils import controller_utils

    env = {skylet_constants.OVERRIDE_CONSOLIDATION_MODE: 'true'}
    if gate_on:
        from sky.utils import env_options
        env[env_options.Options.MEMORY_AWARE_WORKER_SIZING.env_key] = 'true'
    with mock.patch.dict(os.environ, env):
        reserved = controller_utils.compute_memory_reserved_for_controllers(
            reserve_extra_for_pool=True)
    assert reserved == float(
        controller_utils.MAXIMUM_CONTROLLER_RESERVED_MEMORY_MB) * (
            1 + controller_utils.POOL_JOBS_RESOURCES_RATIO)


@mock.patch('sky.utils.common_utils.get_mem_size_gb', return_value=48)
@mock.patch('sky.utils.common_utils.get_cpu_count', return_value=12)
def test_no_controller_reservation_outside_consolidation(
        cpu_count, mem_size_gb):
    """Outside consolidation mode the controllers cost the API server nothing."""
    from sky.utils import controller_utils

    with _memory_aware_sizing(), \
         mock.patch.object(controller_utils, 'is_jobs_consolidation_mode',
                           return_value=False), \
         mock.patch.object(controller_utils, '_is_consolidation_mode',
                           return_value=False):
        assert controller_utils.compute_memory_reserved_for_controllers(
            reserve_extra_for_pool=True) == 0.0


# Shapes big enough that the _MIN_LONG_WORKERS / _get_min_short_workers
# responsiveness floors do not override the memory budget.
@pytest.mark.parametrize('cpu_count,mem_size_gb', [(4, 8), (4, 16), (8, 32),
                                                   (12, 48), (24, 96),
                                                   (64, 128), (196, 784)])
def test_permanent_processes_fit_in_memory(cpu_count, mem_size_gb):
    """Server workers plus both executor pools must fit in the server memory."""
    with _memory_aware_sizing(), \
         mock.patch('sky.utils.common_utils.get_cpu_count',
                    return_value=cpu_count), \
         mock.patch('sky.utils.common_utils.get_mem_size_gb',
                    return_value=mem_size_gb), \
         mock.patch('sky.jobs.utils.is_consolidation_mode', return_value=False):
        c = config.compute_server_config(deploy=True, quiet=True)
    assert _permanent_worker_memory_gb(c) <= mem_size_gb


def test_parallel_size_long():
    # Test with insufficient memory
    cpu_count = 4
    mem_size_gb = 2
    expected = 1
    assert config._max_long_worker_parallism(cpu_count, mem_size_gb) == expected

    # Test with sufficient memory
    cpu_count = 4
    mem_size_gb = 12.5
    expected = 8
    assert config._max_long_worker_parallism(cpu_count, mem_size_gb) == expected

    # Test with limited memory
    cpu_count = 4
    mem_size_gb = 2.7
    expected = 1
    assert config._max_long_worker_parallism(cpu_count, mem_size_gb) == expected


def test_parallel_size_short():
    # Test with insufficient memory
    blocking_size = 1
    mem_size_gb = 2
    expected = 6
    assert config._max_short_worker_parallism(mem_size_gb,
                                              blocking_size) == expected

    # Test with sufficient memory
    blocking_size = 8
    mem_size_gb = 12.5
    expected = 24
    assert config._max_short_worker_parallism(mem_size_gb,
                                              blocking_size) == expected

    # Test with limited memory
    blocking_size = 1
    mem_size_gb = 3
    expected = 6
    assert config._max_short_worker_parallism(mem_size_gb,
                                              blocking_size) == expected


def test_short_worker_minimum_reserves_every_internal_daemon():
    active_daemon_count = sum(
        not daemon.should_skip() for daemon in daemons.INTERNAL_REQUEST_DAEMONS)

    assert config._get_min_short_workers() == active_daemon_count + 1


class TestExternalProxyConfig:
    """Test cases for ExternalProxyConfig dataclass."""

    def test_default_values(self):
        """Test default configuration values."""
        proxy_config = config.ExternalProxyConfig()
        assert proxy_config.enabled is False
        assert proxy_config.header_name == 'X-Auth-Request-Email'
        assert proxy_config.header_format == 'plaintext'
        assert proxy_config.jwt_identity_claim == 'sub'

    def test_custom_values(self):
        """Test custom configuration values."""
        proxy_config = config.ExternalProxyConfig(
            enabled=True,
            header_name='x-amzn-oidc-data',
            header_format='jwt',
            jwt_identity_claim='email',
        )
        assert proxy_config.enabled is True
        assert proxy_config.header_name == 'x-amzn-oidc-data'
        assert proxy_config.header_format == 'jwt'
        assert proxy_config.jwt_identity_claim == 'email'


class TestLoadServerConfig:
    """Test cases for load_server_config function."""

    def test_load_empty_config(self):
        """Test loading when no config file exists."""
        with mock.patch.object(config, 'SERVER_CONFIG_PATH',
                               '/nonexistent/path'):
            server_config = config.load_server_config()

        assert server_config == {}

    def test_load_config_from_file(self):
        """Test loading configuration from file."""
        yaml_content = """
auth:
  external_proxy:
    enabled: true
    header_name: x-custom
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            f.write(yaml_content)
            f.flush()
            temp_path = f.name

        try:
            with mock.patch.object(config, 'SERVER_CONFIG_PATH', temp_path):
                server_config = config.load_server_config()

            assert server_config.get_nested(
                ('auth', 'external_proxy', 'enabled'), False) is True
            assert server_config.get_nested(
                ('auth', 'external_proxy', 'header_name'), None) == 'x-custom'
        finally:
            os.unlink(temp_path)


def _mock_server_config(config_dict):
    """Helper to create a mock for load_server_config."""
    from sky.utils import config_utils
    return config_utils.Config.from_dict(
        config_dict) if config_dict else config_utils.Config()


class TestLoadExternalProxyConfig:
    """Test cases for load_external_proxy_config function.

    These tests mock load_server_config directly to avoid race conditions
    in parallel test execution. Environment variables are mocked without
    clear=True to avoid affecting other parallel tests.
    """

    def setup_method(self):
        """Clear the lru_cache before each test."""
        config.load_external_proxy_config.cache_clear()

    def teardown_method(self):
        """Clear the lru_cache after each test."""
        config.load_external_proxy_config.cache_clear()

    def test_load_from_server_yaml(self):
        """Test loading configuration from server.yaml file."""
        mock_config = _mock_server_config({
            'auth': {
                'external_proxy': {
                    'enabled': True,
                    'header_name': 'x-amzn-oidc-data',
                    'header_format': 'jwt',
                    'jwt_identity_claim': 'email',
                }
            }
        })
        with mock.patch('sky.server.config.load_server_config',
                        return_value=mock_config):
            with mock.patch.dict(
                    os.environ, {
                        'ENABLE_BASIC_AUTH': '',
                        'SKYPILOT_AUTH_OAUTH2_PROXY_ENABLED': '',
                        'SKYPILOT_AUTH_USER_HEADER': '',
                    }):
                proxy_config = config.load_external_proxy_config()

        assert proxy_config.enabled is True
        assert proxy_config.header_name == 'x-amzn-oidc-data'
        assert proxy_config.header_format == 'jwt'
        assert proxy_config.jwt_identity_claim == 'email'

    def test_load_from_server_yaml_disabled(self):
        """Test loading disabled configuration from server.yaml."""
        mock_config = _mock_server_config(
            {'auth': {
                'external_proxy': {
                    'enabled': False,
                }
            }})
        with mock.patch('sky.server.config.load_server_config',
                        return_value=mock_config):
            with mock.patch.dict(
                    os.environ, {
                        'ENABLE_BASIC_AUTH': '',
                        'SKYPILOT_AUTH_OAUTH2_PROXY_ENABLED': '',
                        'SKYPILOT_AUTH_USER_HEADER': '',
                    }):
                proxy_config = config.load_external_proxy_config()

        assert proxy_config.enabled is False

    def test_default_enabled_when_no_config_and_no_builtin_auth(self):
        """Test that external proxy is enabled by default for backward compat.

        When no config file exists and no built-in auth is enabled, external
        proxy auth should be enabled to support legacy ingress oauth2-proxy.
        """
        mock_config = _mock_server_config({})
        with mock.patch('sky.server.config.load_server_config',
                        return_value=mock_config):
            with mock.patch.dict(
                    os.environ, {
                        'ENABLE_BASIC_AUTH': '',
                        'SKYPILOT_AUTH_OAUTH2_PROXY_ENABLED': '',
                        'SKYPILOT_AUTH_USER_HEADER': '',
                    }):
                proxy_config = config.load_external_proxy_config()

        # Enabled by default for backward compatibility
        assert proxy_config.enabled is True

    def test_default_disabled_when_basic_auth_enabled(self):
        """Test that external proxy is disabled when basic auth is enabled."""
        mock_config = _mock_server_config({})
        with mock.patch('sky.server.config.load_server_config',
                        return_value=mock_config):
            with mock.patch.dict(
                    os.environ, {
                        'ENABLE_BASIC_AUTH': 'true',
                        'SKYPILOT_AUTH_OAUTH2_PROXY_ENABLED': '',
                        'SKYPILOT_AUTH_USER_HEADER': '',
                    }):
                proxy_config = config.load_external_proxy_config()

        # Disabled because built-in basic auth is enabled
        assert proxy_config.enabled is False

    def test_default_disabled_when_oauth2_proxy_on_server_enabled(self):
        """Test that external proxy is disabled when oauth2-proxy on server."""
        mock_config = _mock_server_config({})
        with mock.patch('sky.server.config.load_server_config',
                        return_value=mock_config):
            with mock.patch.dict(
                    os.environ, {
                        'ENABLE_BASIC_AUTH': '',
                        'SKYPILOT_AUTH_OAUTH2_PROXY_ENABLED': 'true',
                        'SKYPILOT_AUTH_USER_HEADER': '',
                    }):
                proxy_config = config.load_external_proxy_config()

        # Disabled because oauth2-proxy on API server is enabled
        assert proxy_config.enabled is False

    def test_explicit_enabled_overrides_builtin_auth_check(self):
        """Test that explicit enabled=true overrides built-in auth check."""
        mock_config = _mock_server_config(
            {'auth': {
                'external_proxy': {
                    'enabled': True,
                }
            }})
        with mock.patch('sky.server.config.load_server_config',
                        return_value=mock_config):
            # Even with basic auth enabled, explicit config takes precedence
            with mock.patch.dict(
                    os.environ, {
                        'ENABLE_BASIC_AUTH': 'true',
                        'SKYPILOT_AUTH_OAUTH2_PROXY_ENABLED': '',
                        'SKYPILOT_AUTH_USER_HEADER': '',
                    }):
                proxy_config = config.load_external_proxy_config()

        assert proxy_config.enabled is True

    def test_explicit_disabled_when_no_builtin_auth(self):
        """Test that explicit enabled=false is respected."""
        mock_config = _mock_server_config(
            {'auth': {
                'external_proxy': {
                    'enabled': False,
                }
            }})
        with mock.patch('sky.server.config.load_server_config',
                        return_value=mock_config):
            with mock.patch.dict(
                    os.environ, {
                        'ENABLE_BASIC_AUTH': '',
                        'SKYPILOT_AUTH_OAUTH2_PROXY_ENABLED': '',
                        'SKYPILOT_AUTH_USER_HEADER': '',
                    }):
                proxy_config = config.load_external_proxy_config()

        # Explicit false is respected even without built-in auth
        assert proxy_config.enabled is False

    def test_legacy_env_var_overrides_header_name(self):
        """Test that legacy env var overrides header_name from config."""
        mock_config = _mock_server_config({
            'auth': {
                'external_proxy': {
                    'enabled': True,
                    'header_name': 'x-from-yaml',
                    'header_format': 'plaintext',
                }
            }
        })
        with mock.patch('sky.server.config.load_server_config',
                        return_value=mock_config):
            with mock.patch.dict(
                    os.environ, {
                        'ENABLE_BASIC_AUTH': '',
                        'SKYPILOT_AUTH_OAUTH2_PROXY_ENABLED': '',
                        'SKYPILOT_AUTH_USER_HEADER': 'X-From-Env',
                    }):
                proxy_config = config.load_external_proxy_config()

        # Legacy env var should override header_name
        assert proxy_config.header_name == 'X-From-Env'

    def test_legacy_env_var_with_jwt_format_raises_error(self):
        """Test that legacy env var with JWT format raises ValueError."""
        mock_config = _mock_server_config({
            'auth': {
                'external_proxy': {
                    'enabled': True,
                    'header_name': 'x-amzn-oidc-data',
                    'header_format': 'jwt',
                }
            }
        })
        with mock.patch('sky.server.config.load_server_config',
                        return_value=mock_config):
            with mock.patch.dict(
                    os.environ, {
                        'ENABLE_BASIC_AUTH': '',
                        'SKYPILOT_AUTH_OAUTH2_PROXY_ENABLED': '',
                        'SKYPILOT_AUTH_USER_HEADER': 'X-From-Env',
                    }):
                with pytest.raises(ValueError) as exc_info:
                    config.load_external_proxy_config()

        assert 'header_format is "jwt"' in str(exc_info.value)
        assert 'SKYPILOT_AUTH_USER_HEADER' in str(exc_info.value)

    def test_empty_server_yaml_defaults_to_enabled(self):
        """Test that empty server.yaml defaults to enabled for backward compat.

        When server.yaml exists but doesn't set enabled, and no built-in auth
        is active, external proxy should be enabled for backward compatibility
        with legacy ingress oauth2-proxy setups.
        """
        mock_config = _mock_server_config({})
        with mock.patch('sky.server.config.load_server_config',
                        return_value=mock_config):
            with mock.patch.dict(
                    os.environ, {
                        'ENABLE_BASIC_AUTH': '',
                        'SKYPILOT_AUTH_OAUTH2_PROXY_ENABLED': '',
                        'SKYPILOT_AUTH_USER_HEADER': '',
                    }):
                proxy_config = config.load_external_proxy_config()

        # Enabled by default for backward compatibility
        assert proxy_config.enabled is True

    def test_empty_server_yaml_disabled_when_basic_auth(self):
        """Test that empty server.yaml with basic auth returns disabled.

        When server.yaml exists but doesn't set enabled, and built-in basic
        auth is active, external proxy should be disabled.
        """
        mock_config = _mock_server_config({})
        with mock.patch('sky.server.config.load_server_config',
                        return_value=mock_config):
            with mock.patch.dict(
                    os.environ, {
                        'ENABLE_BASIC_AUTH': 'true',
                        'SKYPILOT_AUTH_OAUTH2_PROXY_ENABLED': '',
                        'SKYPILOT_AUTH_USER_HEADER': '',
                    }):
                proxy_config = config.load_external_proxy_config()

        # Disabled because built-in basic auth is enabled
        assert proxy_config.enabled is False

    def test_partial_server_yaml_uses_defaults_for_missing(self):
        """Test that partial config uses defaults for missing fields."""
        mock_config = _mock_server_config({
            'auth': {
                'external_proxy': {
                    'enabled': True,
                    'header_name': 'x-custom-header',
                }
            }
        })
        with mock.patch('sky.server.config.load_server_config',
                        return_value=mock_config):
            with mock.patch.dict(
                    os.environ, {
                        'ENABLE_BASIC_AUTH': '',
                        'SKYPILOT_AUTH_OAUTH2_PROXY_ENABLED': '',
                        'SKYPILOT_AUTH_USER_HEADER': '',
                    }):
                proxy_config = config.load_external_proxy_config()

        # Custom value from yaml
        assert proxy_config.header_name == 'x-custom-header'
        assert proxy_config.enabled is True
        # Defaults for missing fields
        assert proxy_config.header_format == 'plaintext'
        assert proxy_config.jwt_identity_claim == 'sub'


class TestExtractIdentityFromJwt:
    """Test cases for JWT identity extraction in server.py."""

    def test_extract_email_from_valid_jwt(self):
        """Test extracting email claim from a valid JWT."""
        from sky.server.server import _extract_identity_from_jwt

        # Create a test JWT
        payload = {'email': 'test@example.com', 'sub': '12345'}
        token = pyjwt.encode(payload, 'secret', algorithm='HS256')

        result = _extract_identity_from_jwt(token, 'email')
        assert result == 'test@example.com'

    def test_extract_sub_from_valid_jwt(self):
        """Test extracting sub claim from a valid JWT."""
        from sky.server.server import _extract_identity_from_jwt

        payload = {'email': 'test@example.com', 'sub': '12345'}
        token = pyjwt.encode(payload, 'secret', algorithm='HS256')

        result = _extract_identity_from_jwt(token, 'sub')
        assert result == '12345'

    def test_extract_missing_claim_returns_none(self):
        """Test extracting a missing claim returns None."""
        from sky.server.server import _extract_identity_from_jwt

        payload = {'sub': '12345'}
        token = pyjwt.encode(payload, 'secret', algorithm='HS256')

        result = _extract_identity_from_jwt(token, 'email')
        assert result is None

    def test_invalid_jwt_returns_none(self):
        """Test that an invalid JWT returns None."""
        from sky.server.server import _extract_identity_from_jwt

        result = _extract_identity_from_jwt('not-a-valid-jwt', 'email')
        assert result is None

    def test_empty_jwt_returns_none(self):
        """Test that an empty JWT returns None."""
        from sky.server.server import _extract_identity_from_jwt

        result = _extract_identity_from_jwt('', 'email')
        assert result is None


class TestExtractUserFromHeader:
    """Test cases for user extraction from headers."""

    def test_extract_from_plaintext_header(self):
        """Test extracting user from plaintext header."""
        from sky.server.server import _extract_user_from_header

        request = mock.MagicMock()
        request.headers = {'X-Auth-Request-Email': 'user@example.com'}

        proxy_config = config.ExternalProxyConfig(
            enabled=True,
            header_name='X-Auth-Request-Email',
            header_format='plaintext',
        )

        user = _extract_user_from_header(request, proxy_config)

        assert user is not None
        assert user.name == 'user@example.com'
        assert user.id is not None

    def test_extract_from_jwt_header(self):
        """Test extracting user from JWT header."""
        from sky.server.server import _extract_user_from_header

        payload = {'email': 'jwt-user@example.com'}
        token = pyjwt.encode(payload, 'secret', algorithm='HS256')

        request = mock.MagicMock()
        request.headers = {'x-amzn-oidc-data': token}

        proxy_config = config.ExternalProxyConfig(
            enabled=True,
            header_name='x-amzn-oidc-data',
            header_format='jwt',
            jwt_identity_claim='email',
        )

        user = _extract_user_from_header(request, proxy_config)

        assert user is not None
        assert user.name == 'jwt-user@example.com'

    def test_missing_header_returns_none(self):
        """Test that missing header returns None."""
        from sky.server.server import _extract_user_from_header

        request = mock.MagicMock()
        request.headers = {}

        proxy_config = config.ExternalProxyConfig(
            enabled=True,
            header_name='X-Auth-Request-Email',
            header_format='plaintext',
        )

        user = _extract_user_from_header(request, proxy_config)

        assert user is None

    def test_invalid_jwt_in_header_returns_none(self):
        """Test that invalid JWT in header returns None."""
        from sky.server.server import _extract_user_from_header

        request = mock.MagicMock()
        request.headers = {'x-amzn-oidc-data': 'not-a-jwt'}

        proxy_config = config.ExternalProxyConfig(
            enabled=True,
            header_name='x-amzn-oidc-data',
            header_format='jwt',
            jwt_identity_claim='email',
        )

        user = _extract_user_from_header(request, proxy_config)

        assert user is None

    def test_jwt_with_missing_claim_returns_none(self):
        """Test that JWT with missing claim returns None."""
        from sky.server.server import _extract_user_from_header

        payload = {'sub': '12345'}  # No email claim
        token = pyjwt.encode(payload, 'secret', algorithm='HS256')

        request = mock.MagicMock()
        request.headers = {'x-amzn-oidc-data': token}

        proxy_config = config.ExternalProxyConfig(
            enabled=True,
            header_name='x-amzn-oidc-data',
            header_format='jwt',
            jwt_identity_claim='email',
        )

        user = _extract_user_from_header(request, proxy_config)

        assert user is None
