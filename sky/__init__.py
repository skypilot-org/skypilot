"""The SkyPilot package."""
import functools
import importlib
import importlib.abc
import importlib.util
import os
import subprocess
import sys
from typing import Any, Dict, Optional, TYPE_CHECKING
import urllib.request

from sky.utils import directory_utils

if TYPE_CHECKING:
    from importlib.metadata import EntryPoint

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
__version__ = '0.12.4'
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
from sky import batch  # noqa: F401 # pylint: disable=unused-import
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
from sky.client.sdk import create_debug_dump
from sky.client.sdk import down
from sky.client.sdk import download_debug_dump
from sky.client.sdk import download_logs
from sky.client.sdk import endpoints
from sky.client.sdk import exec  # pylint: disable=redefined-builtin
from sky.client.sdk import get
from sky.client.sdk import get_user_workspace
from sky.client.sdk import job_status
from sky.client.sdk import launch
from sky.client.sdk import optimize
from sky.client.sdk import queue
from sky.client.sdk import reload_config
from sky.client.sdk import set_preferred_workspace
from sky.client.sdk import start
from sky.client.sdk import status
from sky.client.sdk import stop
from sky.client.sdk import storage_delete
from sky.client.sdk import storage_ls
from sky.client.sdk import stream_and_get
from sky.client.sdk import tail_logs
from sky.dag import Dag
from sky.dag import DagExecution
from sky.data import FileMountType
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
Slurm = clouds.Slurm
Kubernetes = clouds.Kubernetes
K8s = Kubernetes
SSH = clouds.SSH
OCI = clouds.OCI
Paperspace = clouds.Paperspace
PrimeIntellect = clouds.PrimeIntellect
RunPod = clouds.RunPod
Vast = clouds.Vast
Vsphere = clouds.Vsphere
Fluidstack = clouds.Fluidstack
Nebius = clouds.Nebius
Hyperbolic = clouds.Hyperbolic
Mithril = clouds.Mithril
Shadeform = clouds.Shadeform
Seeweb = clouds.Seeweb
Yotta = clouds.Yotta
Verda = clouds.Verda

__all__ = [
    '__version__',
    'AWS',
    'Azure',
    'Cudo',
    'GCP',
    'IBM',
    'Kubernetes',
    'K8s',
    'SSH',
    'Lambda',
    'OCI',
    'Paperspace',
    'PrimeIntellect',
    'RunPod',
    'Vast',
    'SCP',
    'Slurm',
    'Vsphere',
    'Fluidstack',
    'Nebius',
    'Hyperbolic',
    'Mithril',
    'Shadeform',
    'Seeweb',
    'Yotta',
    'Optimizer',
    'OptimizeTarget',
    'backends',
    'list_accelerators',
    '__root_dir__',
    'FileMountType',
    'Storage',
    'StorageMode',
    'StoreType',
    'ClusterStatus',
    'JobStatus',
    'ManagedJobStatus',
    'StatusRefreshMode',
    # APIs
    'Dag',
    'DagExecution',
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
    'set_preferred_workspace',
    'get_user_workspace',
    'api_login',
    'api_start',
    'api_stop',
    'api_server_logs',
    # Debug Dump
    'create_debug_dump',
    'download_debug_dump',
    # Admin Policy
    'UserRequest',
    'MutatedUserRequest',
    'AdminPolicy',
    'Config',
    'AdminPolicyRequestName',
    # Registry
    'CLOUD_REGISTRY',
    'JOBS_RECOVERY_STRATEGY_REGISTRY',
    # Batch processing
    'batch',
]

# --------------------- Client SDK namespace --------------------- #
# Installed packages can surface a client SDK *module* under the ``sky``
# namespace by registering an entry point in the ``sky.client_sdks`` group
# whose value is the dotted path of the module to expose:
#
#     # in the providing package's setup.py / pyproject.toml
#     entry_points={'sky.client_sdks': ['foo = some_package.foo.sdk']}
#
# ``sky.foo``, ``from sky import foo`` and ``import sky.foo`` then all resolve
# (lazily) to ``some_package.foo.sdk``, letting add-on packages extend the
# ``sky`` namespace without SkyPilot having to know about them at build time.
# This mirrors the other entry-point hooks SkyPilot already exposes. The entry
# point exposes a single module, not a sub-namespace: ``sky.foo.bar`` is not
# aliased (it raises the usual "not a package" error for a module target).
_CLIENT_SDK_ENTRY_POINT_GROUP = 'sky.client_sdks'


@functools.lru_cache(maxsize=1)
def _client_sdk_entry_points() -> Dict[str, 'EntryPoint']:
    """Map of name -> entry point in the ``sky.client_sdks`` group.

    Cached for the process lifetime: ``importlib.metadata.entry_points()``
    scans every installed distribution, which is far too expensive to repeat
    on each attribute miss or unresolved ``sky.*`` import.
    """
    # Imported lazily: importlib.metadata adds measurable time to ``import
    # sky`` and is only needed the first time a contributed SDK is resolved.
    # pylint: disable-next=import-outside-toplevel
    import importlib.metadata as importlib_metadata
    all_entry_points = importlib_metadata.entry_points()
    # ``entry_points().select(group=...)`` is the API on Python 3.10+ (and the
    # only one on 3.12+). On 3.8/3.9 ``entry_points()`` returns a plain mapping
    # of group name to entry points, so fall back to ``.get()`` there.
    select = getattr(all_entry_points, 'select', None)
    if select is not None:
        group_entry_points = select(group=_CLIENT_SDK_ENTRY_POINT_GROUP)
    else:
        group_entry_points = all_entry_points.get(_CLIENT_SDK_ENTRY_POINT_GROUP,
                                                  [])
    return {ep.name: ep for ep in group_entry_points}


def _find_client_sdk_entry_point(name: str) -> Optional['EntryPoint']:
    """Return the ``sky.client_sdks`` entry point named ``name``, if any."""
    return _client_sdk_entry_points().get(name)


class _ClientSdkLoader(importlib.abc.Loader):
    """importlib loader that aliases ``sky.<name>`` to an existing module."""

    def __init__(self, target_module: str) -> None:
        self._target_module = target_module
        self._original_spec: Any = None

    def create_module(self, spec: Any) -> Any:
        del spec  # Unused; the module name comes from the registered target.
        # Return the target module itself so that, e.g.,
        # ``sky.foo is some_package.foo.sdk`` and it is not executed again
        # under a second name.
        target = importlib.import_module(self._target_module)
        # Capture the target's real spec now: module_from_spec() will shortly
        # overwrite ``target.__spec__`` with this alias's spec (whose name is
        # ``sky.<name>`` and whose loader is this object). exec_module restores
        # it.
        self._original_spec = getattr(target, '__spec__', None)
        return target

    def exec_module(self, module: Any) -> None:
        # Restore the target's real spec that module_from_spec() just
        # overwrote, so the aliased module keeps a consistent ``__name__`` /
        # ``__spec__.name`` and stays reloadable (importlib.reload keys off
        # ``__spec__``, not ``__name__``).
        if self._original_spec is not None:
            module.__spec__ = self._original_spec


class _ClientSdkFinder(importlib.abc.MetaPathFinder):
    """importlib meta-path finder for entry-point-registered client SDKs.

    Appended to ``sys.meta_path`` so it only runs after the default finders;
    real ``sky`` submodules always take precedence.
    """

    def find_spec(self,
                  fullname: str,
                  path: Any = None,
                  target: Any = None) -> Any:
        del path, target  # Unused; resolution is keyed only on ``fullname``.
        prefix = f'{__name__}.'
        if not fullname.startswith(prefix):
            return None
        name = fullname[len(prefix):]
        # Only top-level ``sky.<name>`` is aliased: the entry point exposes a
        # single module, not a sub-namespace. Submodule imports
        # (``sky.<name>.<sub>``) fall through to the default finders.
        if '.' in name:
            return None
        entry_point = _find_client_sdk_entry_point(name)
        if entry_point is None:
            return None
        return importlib.util.spec_from_loader(
            fullname, _ClientSdkLoader(entry_point.value))


def __getattr__(name: str) -> Any:  # pylint: disable=invalid-name
    """Lazily resolve client SDKs registered under ``sky.client_sdks``.

    PEP 562 module hook, consulted only for attributes not already defined on
    ``sky``, so it never shadows real submodules or symbols. This handles bare
    attribute access (``import sky; sky.foo``), which does not go through the
    meta-path finder above.
    """
    if name.startswith('_'):
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
    if _find_client_sdk_entry_point(name) is None:
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
    # Routes through _ClientSdkFinder, which binds ``sky.<name>`` and registers
    # it in sys.modules; any error importing the SDK propagates unchanged.
    return importlib.import_module(f'{__name__}.{name}')


if not any(isinstance(finder, _ClientSdkFinder) for finder in sys.meta_path):
    sys.meta_path.append(_ClientSdkFinder())
