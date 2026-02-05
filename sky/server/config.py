"""SkyPilot API Server configuration."""

import dataclasses
import enum
import functools
import os
from typing import Literal, Optional

from sky import sky_logging
from sky.server import constants as server_constants
from sky.server import daemons
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import config_utils
from sky.utils import yaml_utils

# Constants based on profiling the peak memory usage while serving various
# sky commands. These estimation are highly related to usage patterns
# (clouds enabled, type of requests, etc. see `tests/load_tests` for details.),
# the profiling covers major clouds and common usage patterns. For user has
# deviated usage pattern, they can override the default estimation by
# environment variables.
# NOTE(dev): update these constants for each release according to the load
# test results.
# TODO(aylei): maintaining these constants is error-prone, we may need to
# automatically tune parallelism at runtime according to system usage stats
# in the future.
# TODO(luca): The future is now! ^^^
LONG_WORKER_MEM_GB = 0.4
SHORT_WORKER_MEM_GB = 0.3
# To control the number of long workers.
_CPU_MULTIPLIER_FOR_LONG_WORKERS = 2
# Limit the number of long workers of local API server, since local server is
# typically:
# 1. launched automatically in an environment with high resource contention
#    (e.g. Laptop)
# 2. used by a single user
_MAX_LONG_WORKERS_LOCAL = 4
# Percentage of memory for long requests
# from the memory reserved for SkyPilot.
# This is to reserve some memory for short requests.
_MAX_MEM_PERCENT_FOR_BLOCKING = 0.6
# Minimal number of long workers to ensure responsiveness.
_MIN_LONG_WORKERS = 1
# Minimal number of idle short workers to ensure responsiveness.
_MIN_IDLE_SHORT_WORKERS = 1

# Default number of burstable workers for local API server. A heuristic number
# that is large enough for most local cases.
# TODO(aylei): the number of burstable workers should be auto-tuned based on the
# system usage stats.
_BURSTABLE_WORKERS_FOR_LOCAL = 1024

SERVER_CONFIG_PATH = '~/.sky/.server.yaml'

_DEFAULT_HEADER_NAME = 'X-Auth-Request-Email'
_DEFAULT_IDENTITY_CLAIM = 'sub'

logger = sky_logging.init_logger(__name__)


class QueueBackend(enum.Enum):
    # Local queue backend serves queues in each process locally, which has
    # lower resource usage but the consumer must be in the same process, i.e.
    # this only works in single-process mode.
    LOCAL = 'local'
    # Multi-process queue backend starts a dedicated process for serving queues.
    MULTIPROCESSING = 'multiprocessing'
    # TODO(zhwu): we can add redis backend in the future.


@dataclasses.dataclass
class WorkerConfig:
    garanteed_parallelism: int
    burstable_parallelism: int
    num_db_connections_per_worker: int


@dataclasses.dataclass
class ServerConfig:
    num_server_workers: int
    long_worker_config: WorkerConfig
    short_worker_config: WorkerConfig
    num_db_connections_per_worker: int
    queue_backend: QueueBackend


def compute_server_config(
        deploy: bool,
        max_db_connections: Optional[int] = None,
        quiet: bool = False,
        reserved_memory_mb: Optional[float] = None) -> ServerConfig:
    """Compute the server config based on environment.

    We have different assumptions for the resources in different deployment
    modes, which leads to different worker setups:

    - Deployment mode (deploy=True), we assume the resources are dedicated to
      the API server and the resources will be tuned for serious use cases, so:
      - Use multiprocessing queue backend and dedicated workers processes to
        avoid GIL contention.
      - Parallelism (number of executor processes) is fixed and executor
        processes have same lifecycle with the server, which ensures
        best-effort cache reusing and stable resources consumption.
      - Reject to start in low resource environments, to avoid flaky
        deployments.
    - Local mode (deploy=False), we assume the server is running in a shared
      environment (e.g. laptop) and users typically do not pay attention to
      the resource setup of the server. Moreover, existing users may expect
      some consistent behaviors with old versions, i.e. before API server was
      introduced, so:
      - The max number of long-running executor processes are limited, to avoid
        high memory consumption when the server is idle.
      - Allow burstable workers to handle requests when all long-running
        workers are busy, which mimics the behavior of local sky CLI before
        API server was introduced.
      - Works in low resources environments, and further reduce the memory
        consumption in low resource environments.

    Note that there is still significant overhead for SDK users when migrate to
    local API server. Since the users are free to run sky operations in Threads
    when using SDK but all client operations will occupy at least one worker
    process after API server was introduced.
    """
    cpu_count = common_utils.get_cpu_count()
    logger.debug(f'CPU count: {cpu_count}')
    mem_size_gb = common_utils.get_mem_size_gb()
    if reserved_memory_mb is not None:
        mem_size_gb -= (reserved_memory_mb / 1024)
    logger.debug(f'Memory size: {mem_size_gb}GB')
    max_parallel_for_long = _max_long_worker_parallism(cpu_count,
                                                       mem_size_gb,
                                                       local=not deploy)
    max_parallel_for_short = _max_short_worker_parallism(
        mem_size_gb, max_parallel_for_long)
    queue_backend = QueueBackend.MULTIPROCESSING
    burstable_parallel_for_long = 0
    burstable_parallel_for_short = 0
    # if num_db_connections_per_worker is 0, server will use NullPool
    # to conserve the number of concurrent db connections.
    # This could lead to performance degradation.
    num_db_connections_per_worker = 0
    num_server_workers = cpu_count

    # +1 for the event loop running the main process
    # and gc daemons in the '__main__' body of sky/server/server.py
    max_parallel_all_workers = (max_parallel_for_long + max_parallel_for_short +
                                num_server_workers + 1)

    if not deploy:
        # For local mode, use local queue backend since we only run 1 uvicorn
        # worker in local mode and no multiprocessing is needed.
        num_server_workers = 1
        queue_backend = QueueBackend.LOCAL
        # Enable burstable workers for local API server.
        burstable_parallel_for_long = _BURSTABLE_WORKERS_FOR_LOCAL
        burstable_parallel_for_short = _BURSTABLE_WORKERS_FOR_LOCAL
        # Runs in low resource mode if the available memory is less than
        # server_constants.MIN_AVAIL_MEM_GB.
        # pylint: disable=import-outside-toplevel
        import sky.jobs.utils as job_utils
        max_memory = (server_constants.MIN_AVAIL_MEM_GB_CONSOLIDATION_MODE
                      if job_utils.is_consolidation_mode() else
                      server_constants.MIN_AVAIL_MEM_GB)
        if not deploy and mem_size_gb < max_memory:
            # Permanent worker process may have significant memory consumption
            # (~350MB per worker) after running commands like `sky check`, so we
            # don't start any permanent workers in low resource local mode. This
            # mimics the behavior of local sky CLI before API server was
            # introduced, where the CLI will start new process everytime and
            # never reject to start due to resource constraints.
            # Note that the refresh daemon will still occupy one worker
            # permanently because it never exits.
            max_parallel_for_long = 0
            max_parallel_for_short = 0
            if not quiet:
                logger.warning(
                    'SkyPilot API server will run in low resource mode because '
                    'the available memory is less than '
                    f'{server_constants.MIN_AVAIL_MEM_GB}GB.')
    elif max_db_connections is not None:
        if max_parallel_all_workers > max_db_connections:
            if not quiet:
                logger.warning(
                    f'Max parallel all workers ({max_parallel_all_workers}) '
                    'is greater than max db connections '
                    f'({max_db_connections}). Increase the number of max db '
                    f'connections to at least {max_parallel_all_workers} for '
                    'optimal performance.')
        else:
            num_db_connections_per_worker = 1

    if not quiet:
        logger.info(
            f'SkyPilot API server will start {num_server_workers} server '
            f'processes with {max_parallel_for_long} background workers for '
            f'long requests and will allow at max {max_parallel_for_short} '
            'short requests in parallel.')
    return ServerConfig(
        num_server_workers=num_server_workers,
        queue_backend=queue_backend,
        long_worker_config=WorkerConfig(
            garanteed_parallelism=max_parallel_for_long,
            burstable_parallelism=burstable_parallel_for_long,
            num_db_connections_per_worker=num_db_connections_per_worker),
        short_worker_config=WorkerConfig(
            garanteed_parallelism=max_parallel_for_short,
            burstable_parallelism=burstable_parallel_for_short,
            num_db_connections_per_worker=num_db_connections_per_worker),
        num_db_connections_per_worker=num_db_connections_per_worker,
    )


def _max_long_worker_parallism(cpu_count: int,
                               mem_size_gb: float,
                               local=False) -> int:
    """Max parallelism for long workers."""
    # Reserve min available memory to avoid OOM.
    # pylint: disable=import-outside-toplevel
    import sky.jobs.utils as job_utils
    max_memory = (server_constants.MIN_AVAIL_MEM_GB_CONSOLIDATION_MODE
                  if job_utils.is_consolidation_mode() else
                  server_constants.MIN_AVAIL_MEM_GB)
    available_mem = max(0, mem_size_gb - max_memory)
    cpu_based_max_parallel = cpu_count * _CPU_MULTIPLIER_FOR_LONG_WORKERS
    mem_based_max_parallel = int(available_mem * _MAX_MEM_PERCENT_FOR_BLOCKING /
                                 LONG_WORKER_MEM_GB)
    n = max(_MIN_LONG_WORKERS,
            min(cpu_based_max_parallel, mem_based_max_parallel))
    if local:
        return min(n, _MAX_LONG_WORKERS_LOCAL)
    return n


def _get_min_short_workers() -> int:
    """Min number of short workers."""
    daemon_count = 0
    for daemon in daemons.INTERNAL_REQUEST_DAEMONS:
        if not daemon.should_skip():
            daemon_count += 1
    return _MIN_IDLE_SHORT_WORKERS + daemon_count


def _max_short_worker_parallism(mem_size_gb: float,
                                long_worker_parallism: int) -> int:
    """Max parallelism for short workers."""
    # Reserve memory for long workers and min available memory.
    # pylint: disable=import-outside-toplevel
    import sky.jobs.utils as job_utils
    max_memory = (server_constants.MIN_AVAIL_MEM_GB_CONSOLIDATION_MODE
                  if job_utils.is_consolidation_mode() else
                  server_constants.MIN_AVAIL_MEM_GB)
    reserved_mem = max_memory + (long_worker_parallism * LONG_WORKER_MEM_GB)
    available_mem = max(0, mem_size_gb - reserved_mem)
    n = max(_get_min_short_workers(), int(available_mem / SHORT_WORKER_MEM_GB))
    return n


def _get_server_config_schema() -> dict:
    """Returns the schema for validating ~/.sky/server.yaml.

    This configuration file is used by the API server to configure
    authentication and other server-specific settings.
    """
    external_proxy_schema = {
        'type': 'object',
        'additionalProperties': False,
        'properties': {
            'enabled': {
                'type': 'boolean',
            },
            'header_name': {
                'type': 'string',
            },
            'header_format': {
                'type': 'string',
                'enum': ['plaintext', 'jwt'],
            },
            'jwt_identity_claim': {
                'type': 'string',
            },
        },
    }

    auth_schema = {
        'type': 'object',
        'additionalProperties': False,
        'properties': {
            'external_proxy': external_proxy_schema,
        },
    }

    return {
        '$schema': 'https://json-schema.org/draft/2020-12/schema',
        'type': 'object',
        'additionalProperties': False,
        'properties': {
            'auth': auth_schema,
        },
    }


@dataclasses.dataclass
class ExternalProxyConfig:
    """Configuration for external authentication proxy.

    Attributes:
        enabled: Whether external proxy authentication is enabled.
        header_name: The HTTP header containing user identity.
        header_format: The format of the header value ('plaintext' or 'jwt').
        jwt_identity_claim: The JWT claim containing user identity (only used
            when header_format is 'jwt').
    """
    enabled: bool = False
    header_name: str = _DEFAULT_HEADER_NAME
    header_format: Literal['plaintext', 'jwt'] = 'plaintext'
    jwt_identity_claim: str = _DEFAULT_IDENTITY_CLAIM


def load_server_config() -> config_utils.Config:
    """Load server configuration from ~/.sky/server.yaml.

    Returns:
        A Config object containing the server configuration.
    """
    config_path = os.path.expanduser(SERVER_CONFIG_PATH)
    if not os.path.exists(config_path):
        return config_utils.Config()

    config_data = yaml_utils.read_yaml(config_path)
    if not config_data:
        return config_utils.Config()

    common_utils.validate_schema(config_data, _get_server_config_schema(),
                                 config_path)
    return config_utils.Config.from_dict(config_data)


@functools.lru_cache(maxsize=1)
def load_external_proxy_config() -> ExternalProxyConfig:
    """Load external proxy configuration.

    Returns:
        ExternalProxyConfig with the resolved configuration.

    Raises:
        ValueError: If both JWT header format and legacy header env var are set
    """
    server_config = load_server_config()

    enabled = server_config.get_nested(('auth', 'external_proxy', 'enabled'),
                                       None)
    if enabled is None:
        # Backward compatibility: the server may be deployed with a legacy
        # config (e.g. legacy helm chart) which will not explicitly enable
        # external proxy auth when needed (e.g. when oauth2-proxy is configured
        # on the ingress). So:
        # - If any of the built-in auth schemes is enabled, disable it since
        # built-in auth is exclusive with auth on the ingress
        # - Otherwise we enable it since user should have at least one ingress
        # auth scheme set in this case.
        enabled = True
        if (os.getenv(constants.ENV_VAR_ENABLE_BASIC_AUTH, 'false') == 'true' or
            (os.getenv(server_constants.OAUTH2_PROXY_ENABLED_ENV_VAR, 'false')
             == 'true')):
            enabled = False
    if not enabled:
        return ExternalProxyConfig(enabled=False)

    header_format = server_config.get_nested(
        ('auth', 'external_proxy', 'header_format'), 'plaintext')
    header_name = server_config.get_nested(
        ('auth', 'external_proxy', 'header_name'), _DEFAULT_HEADER_NAME)
    jwt_identity_claim = server_config.get_nested(
        ('auth', 'external_proxy', 'jwt_identity_claim'),
        _DEFAULT_IDENTITY_CLAIM)

    # Check for legacy env var
    legacy_header = os.getenv(constants.ENV_VAR_SERVER_AUTH_USER_HEADER)
    if legacy_header:
        if header_format == 'jwt':
            raise ValueError(
                'Configuration error: Environment variable '
                f'{constants.ENV_VAR_SERVER_AUTH_USER_HEADER} is set but '
                'header_format is "jwt". The legacy header environment '
                'variable only supports plaintext format. Please either:\n'
                f'  1. Remove the {constants.ENV_VAR_SERVER_AUTH_USER_HEADER} '
                'environment variable, OR\n'
                '  2. Set header_format to "plaintext" in server.yaml')
        # Legacy env var overrides header_name from config
        header_name = legacy_header
        logger.debug(f'Using legacy auth header from env var: {legacy_header}')

    return ExternalProxyConfig(
        enabled=True,
        header_name=header_name,
        header_format=header_format,
        jwt_identity_claim=jwt_identity_claim,
    )
