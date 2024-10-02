"""The SkyPilot package."""
import os
import subprocess
from typing import Optional
import urllib.request

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
__version__ = '1.0.0-dev0'
__root_dir__ = os.path.dirname(os.path.abspath(__file__))


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
from sky import benchmark
from sky import clouds
from sky.admin_policy import AdminPolicy
from sky.admin_policy import MutatedUserRequest
from sky.admin_policy import UserRequest
# from sky.api.sdk import download_logs
from sky.api.sdk import autostop
from sky.api.sdk import cancel
from sky.api.sdk import cost_report
from sky.api.sdk import down
from sky.api.sdk import exec  # pylint: disable=redefined-builtin
from sky.api.sdk import get
from sky.api.sdk import job_status
from sky.api.sdk import launch
from sky.api.sdk import optimize
from sky.api.sdk import queue
from sky.api.sdk import start
from sky.api.sdk import status
from sky.api.sdk import stop
from sky.api.sdk import storage_delete
from sky.api.sdk import storage_ls
from sky.api.sdk import stream_and_get
from sky.api.sdk import tail_logs
from sky.clouds.service_catalog import list_accelerators
from sky.dag import Dag
from sky.data import Storage
from sky.data import StorageMode
from sky.data import StoreType
# TODO (zhwu): These imports are for backward compatibility, and spot APIs
# should be called with `sky.spot.xxx` instead. Remove in release 0.8.0
from sky.jobs.api.sdk import spot_cancel
from sky.jobs.api.sdk import spot_launch
from sky.jobs.api.sdk import spot_queue
from sky.jobs.api.sdk import spot_tail_logs
from sky.optimizer import Optimizer
from sky.resources import Resources
from sky.skylet.job_lib import JobStatus
from sky.skypilot_config import Config
from sky.task import Task
from sky.utils.common import OptimizeTarget
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
OCI = clouds.OCI
Paperspace = clouds.Paperspace
RunPod = clouds.RunPod
Vsphere = clouds.Vsphere
Fluidstack = clouds.Fluidstack

__all__ = [
    '__version__',
    'AWS',
    'Azure',
    'Cudo',
    'GCP',
    'IBM',
    'Kubernetes',
    'Lambda',
    'OCI',
    'Paperspace',
    'RunPod',
    'SCP',
    'Vsphere',
    'Fluidstack',
    'Optimizer',
    'OptimizeTarget',
    'backends',
    'benchmark',
    'list_accelerators',
    '__root_dir__',
    'Storage',
    'StorageMode',
    'StoreType',
    'ClusterStatus',
    'JobStatus',
    # APIs
    'Dag',
    'Task',
    'Resources',
    # core APIs
    'launch',
    'exec',
    'spot_launch',
    'status',
    'start',
    'stop',
    'down',
    'autostop',
    'cost_report',
    # core APIs Job Management
    'queue',
    'cancel',
    'tail_logs',
    'spot_tail_logs',
    # 'download_logs',
    'job_status',
    # core APIs Spot Job Management
    'spot_queue',
    'spot_cancel',
    'spot_tail_logs',
    # core APIs Storage Management
    'storage_ls',
    'storage_delete',
    # Request APIs
    'get',
    'stream_and_get',
    # Admin Policy
    'UserRequest',
    'MutatedUserRequest',
    'AdminPolicy',
    'Config',
]
