"""The SkyPilot package."""
import os
import subprocess
from typing import Optional
import urllib.request

from sky.utils import directory_utils

# Replaced with the current commit when building the wheels.
_SKYPILOT_COMMIT_SHA = '{{SKYPILOT_COMMIT_SHA}}'


def _get_git_commit():
    if 'SKYPILOT_COMMIT_SHA' not in _SKYPILOT_COMMIT_SHA:
        # This is a release build, so we don't need to get the commit hash from
        # git, as it's already been set.
        return _SKYPILOT_COMMIT_SHA

    # This is a development build (pip install -e .), so we need to get the
    # commit hash from git.
    try:
        cwd = os.path.dirname(__file__)
        commit_hash = subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'],
            cwd=cwd,
            universal_newlines=True,
            stderr=subprocess.DEVNULL).strip()
        changes = subprocess.check_output(['git', 'status', '--porcelain'],
                                          cwd=cwd,
                                          universal_newlines=True,
                                          stderr=subprocess.DEVNULL).strip()
        if changes:
            commit_hash += '-dirty'
        return commit_hash
    except Exception:  # pylint: disable=broad-except
        return _SKYPILOT_COMMIT_SHA


__commit__ = _get_git_commit()
__version__ = '0.10.6'
__root_dir__ = directory_utils.get_sky_dir()


# ---------------------- Proxy Configuration ---------------------- #
def _set_http_proxy_env_vars() -> None:
    urllib_proxies = dict(urllib.request.getproxies())

    def set_proxy_env_var(proxy_var: str, urllib_var: Optional[str]):
        """Sets proxy env vars in os.environ, consulting urllib if needed.

        Logic:
        - If either PROXY_VAR or proxy_var is set in os.environ, set both to the
          same value in os.environ.
        - Else, if urllib_var is set in urllib.request.getproxies(), use that
          value to set PROXY_VAR and proxy_var in os.environ.

        Although many of our underlying libraries are case-insensitive when it
        comes to proxy environment variables, some are not. This has happened to
        GCP's SDK not respecting certain VPN-related proxy env vars.

        This function ensures that both the upper and lower case versions of the
        proxy environment variables are set if either is set to ensure maximum
        compatibility.
        """
        # Check for the uppercase version first
        proxy = os.getenv(proxy_var.upper(), os.getenv(proxy_var.lower()))
        if proxy is None and urllib_var is not None:
            proxy = urllib_proxies.get(urllib_var)

        if proxy is not None:
            os.environ[proxy_var.lower()] = proxy
            os.environ[proxy_var.upper()] = proxy

    set_proxy_env_var('http_proxy', 'http')
    set_proxy_env_var('https_proxy', 'https')
    set_proxy_env_var('all_proxy', None)


_set_http_proxy_env_vars()

# ----------------------------------------------------------------- #

# Keep this order to avoid cyclic imports
# pylint: disable=wrong-import-position
from sky import backends
from sky import clouds
from sky.admin_policy import AdminPolicy
from sky.admin_policy import MutatedUserRequest
from sky.admin_policy import UserRequest
from sky.catalog import list_accelerators
from sky.client.sdk import api_cancel
from sky.client.sdk import api_info
from sky.client.sdk import api_login
from sky.client.sdk import api_server_logs
from sky.client.sdk import api_start
from sky.client.sdk import api_status
from sky.client.sdk import api_stop
from sky.client.sdk import autostop
from sky.client.sdk import cancel
from sky.client.sdk import cost_report
from sky.client.sdk import down
from sky.client.sdk import download_logs
from sky.client.sdk import endpoints
from sky.client.sdk import exec  # pylint: disable=redefined-builtin
from sky.client.sdk import get
from sky.client.sdk import job_status
from sky.client.sdk import launch
from sky.client.sdk import optimize
from sky.client.sdk import queue
from sky.client.sdk import reload_config
from sky.client.sdk import start
from sky.client.sdk import status
from sky.client.sdk import stop
from sky.client.sdk import storage_delete
from sky.client.sdk import storage_ls
from sky.client.sdk import stream_and_get
from sky.client.sdk import tail_logs
from sky.dag import Dag
from sky.data import Storage
from sky.data import StorageMode
from sky.data import StoreType
from sky.jobs import ManagedJobStatus
from sky.optimizer import Optimizer
from sky.resources import Resources
from sky.server.requests.request_names import AdminPolicyRequestName
from sky.skylet.job_lib import JobStatus
from sky.task import Task
from sky.utils.common import OptimizeTarget
from sky.utils.common import StatusRefreshMode
from sky.utils.config_utils import Config
from sky.utils.registry import CLOUD_REGISTRY
from sky.utils.registry import JOBS_RECOVERY_STRATEGY_REGISTRY
from sky.utils.status_lib import ClusterStatus

# Aliases.
IBM = clouds.IBM
AWS = clouds.AWS
Azure = clouds.Azure
Cudo = clouds.Cudo
GCP = clouds.GCP
Lambda = clouds.Lambda
SCP = clouds.SCP
Kubernetes = clouds.Kubernetes
K8s = Kubernetes
OCI = clouds.OCI
Paperspace = clouds.Paperspace
PrimeIntellect = clouds.PrimeIntellect
RunPod = clouds.RunPod
Vast = clouds.Vast
Vsphere = clouds.Vsphere
Fluidstack = clouds.Fluidstack
Nebius = clouds.Nebius
Hyperbolic = clouds.Hyperbolic
Shadeform = clouds.Shadeform
Seeweb = clouds.Seeweb

__all__ = [
    '__version__',
    'AWS',
    'Azure',
    'Cudo',
    'GCP',
    'IBM',
    'Kubernetes',
    'K8s',
    'Lambda',
    'OCI',
    'Paperspace',
    'PrimeIntellect',
    'RunPod',
    'Vast',
    'SCP',
    'Vsphere',
    'Fluidstack',
    'Nebius',
    'Hyperbolic',
    'Shadeform',
    'Seeweb',
    'Optimizer',
    'OptimizeTarget',
    'backends',
    'list_accelerators',
    '__root_dir__',
    'Storage',
    'StorageMode',
    'StoreType',
    'ClusterStatus',
    'JobStatus',
    'ManagedJobStatus',
    'StatusRefreshMode',
    # APIs
    'Dag',
    'Task',
    'Resources',
    # core APIs
    'optimize',
    'launch',
    'exec',
    'reload_config',
    # core APIs
    'status',
    'start',
    'stop',
    'down',
    'autostop',
    'cost_report',
    'endpoints',
    # core APIs Job Management
    'queue',
    'cancel',
    'tail_logs',
    'download_logs',
    'job_status',
    # core APIs Storage Management
    'storage_ls',
    'storage_delete',
    # API server APIs
    'get',
    'stream_and_get',
    'api_status',
    'api_cancel',
    'api_info',
    'api_login',
    'api_start',
    'api_stop',
    'api_server_logs',
    # Admin Policy
    'UserRequest',
    'MutatedUserRequest',
    'AdminPolicy',
    'Config',
    'AdminPolicyRequestName',
    # Registry
    'CLOUD_REGISTRY',
    'JOBS_RECOVERY_STRATEGY_REGISTRY',
]
