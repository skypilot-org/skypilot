"""Payloads for the Sky API requests.

TODO(zhwu): We can consider a better way to handle the default values of the
kwargs for the payloads, otherwise, we have to keep the default values the sync
with the backend functions. The benefit of having the default values in the
payloads is that a user can find the default values in the Restful API docs.
"""
import getpass
import json
import os
import typing
from typing import Any, Dict, List, Optional, Tuple, Union

from sky import admin_policy
from sky import serve
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import common as adaptors_common
from sky.server import common
from sky.skylet import constants
from sky.usage import constants as usage_constants
from sky.usage import usage_lib
from sky.utils import annotations
from sky.utils import common as common_lib
from sky.utils import common_utils
from sky.utils import registry

if typing.TYPE_CHECKING:
    import pydantic
else:
    pydantic = adaptors_common.LazyImport('pydantic')

logger = sky_logging.init_logger(__name__)


@annotations.lru_cache(scope='global')
def request_body_env_vars() -> dict:
    env_vars = {}
    for env_var in os.environ:
        if env_var.startswith(constants.SKYPILOT_ENV_VAR_PREFIX):
            env_vars[env_var] = os.environ[env_var]
    env_vars[constants.USER_ID_ENV_VAR] = common_utils.get_user_hash()
    env_vars[constants.USER_ENV_VAR] = os.getenv(constants.USER_ENV_VAR,
                                                 getpass.getuser())
    env_vars[
        usage_constants.USAGE_RUN_ID_ENV_VAR] = usage_lib.messages.usage.run_id
    # Remove the path to config file, as the config content is included in the
    # request body and will be merged with the config on the server side.
    env_vars.pop(skypilot_config.ENV_VAR_SKYPILOT_CONFIG, None)
    env_vars.pop(skypilot_config.ENV_VAR_USER_CONFIG, None)
    env_vars.pop(skypilot_config.ENV_VAR_PROJECT_CONFIG, None)
    return env_vars


def get_override_skypilot_config_from_client() -> Dict[str, Any]:
    """Returns the override configs from the client."""
    config = skypilot_config.to_dict()
    # Remove the API server config, as we should not specify the SkyPilot
    # server endpoint on the server side. This avoids the warning below.
    config.pop_nested(('api_server',), default_value=None)
    ignored_key_values = {}
    for nested_key in constants.SKIPPED_CLIENT_OVERRIDE_KEYS:
        value = config.pop_nested(nested_key, default_value=None)
        if value is not None:
            ignored_key_values['.'.join(nested_key)] = value
    if ignored_key_values:
        logger.debug(f'The following keys ({json.dumps(ignored_key_values)}) '
                     'are specified in the client SkyPilot config at '
                     f'{skypilot_config.loaded_config_path()!r}. '
                     'This will be ignored. If you want to specify it, '
                     'please modify it on server side or contact your '
                     'administrator.')
    return config


class RequestBody(pydantic.BaseModel):
    """The request body for the SkyPilot API."""
    env_vars: Dict[str, str] = {}
    entrypoint: str = ''
    entrypoint_command: str = ''
    using_remote_api_server: bool = False
    override_skypilot_config: Optional[Dict[str, Any]] = {}

    def __init__(self, **data):
        data['env_vars'] = data.get('env_vars', request_body_env_vars())
        usage_lib_entrypoint = usage_lib.messages.usage.entrypoint
        if usage_lib_entrypoint is None:
            usage_lib_entrypoint = ''
        data['entrypoint'] = data.get('entrypoint', usage_lib_entrypoint)
        data['entrypoint_command'] = data.get(
            'entrypoint_command', common_utils.get_pretty_entrypoint_cmd())
        data['using_remote_api_server'] = data.get(
            'using_remote_api_server', not common.is_api_server_local())
        data['override_skypilot_config'] = data.get(
            'override_skypilot_config',
            get_override_skypilot_config_from_client())
        super().__init__(**data)

    def to_kwargs(self) -> Dict[str, Any]:
        """Convert the request body to a kwargs dictionary on API server.

        This converts the request body into kwargs for the underlying SkyPilot
        backend's function.
        """
        kwargs = self.model_dump()
        kwargs.pop('env_vars')
        kwargs.pop('entrypoint')
        kwargs.pop('entrypoint_command')
        kwargs.pop('using_remote_api_server')
        kwargs.pop('override_skypilot_config')
        return kwargs

    @property
    def user_hash(self) -> Optional[str]:
        return self.env_vars.get(constants.USER_ID_ENV_VAR)


class CheckBody(RequestBody):
    """The request body for the check endpoint."""
    clouds: Optional[Tuple[str, ...]] = None
    verbose: bool = False


class DagRequestBody(RequestBody):
    """Request body base class for endpoints with a dag."""
    dag: str

    def to_kwargs(self) -> Dict[str, Any]:
        # Import here to avoid requirement of the whole SkyPilot dependency on
        # local clients.
        # pylint: disable=import-outside-toplevel
        from sky.utils import dag_utils

        kwargs = super().to_kwargs()

        dag = dag_utils.load_chain_dag_from_yaml_str(self.dag)
        # We should not validate the dag here, as the file mounts are not
        # processed yet, but we need to validate the resources during the
        # optimization to make sure the resources are available.
        kwargs['dag'] = dag
        return kwargs


class ValidateBody(DagRequestBody):
    """The request body for the validate endpoint."""
    dag: str
    request_options: Optional[admin_policy.RequestOptions]


class OptimizeBody(DagRequestBody):
    """The request body for the optimize endpoint."""
    dag: str
    minimize: common_lib.OptimizeTarget = common_lib.OptimizeTarget.COST
    request_options: Optional[admin_policy.RequestOptions]


class LaunchBody(RequestBody):
    """The request body for the launch endpoint."""
    task: str
    cluster_name: str
    retry_until_up: bool = False
    idle_minutes_to_autostop: Optional[int] = None
    dryrun: bool = False
    down: bool = False
    backend: Optional[str] = None
    optimize_target: common_lib.OptimizeTarget = common_lib.OptimizeTarget.COST
    no_setup: bool = False
    clone_disk_from: Optional[str] = None
    fast: bool = False
    # Internal only:
    # pylint: disable=invalid-name
    quiet_optimizer: bool = False
    is_launched_by_jobs_controller: bool = False
    is_launched_by_sky_serve_controller: bool = False
    disable_controller_check: bool = False

    def to_kwargs(self) -> Dict[str, Any]:

        kwargs = super().to_kwargs()
        dag = common.process_mounts_in_task_on_api_server(self.task,
                                                          self.env_vars,
                                                          workdir_only=False)

        backend_cls = registry.BACKEND_REGISTRY.from_str(self.backend)
        backend = backend_cls() if backend_cls is not None else None
        kwargs['task'] = dag
        kwargs['backend'] = backend
        kwargs['_quiet_optimizer'] = kwargs.pop('quiet_optimizer')
        kwargs['_is_launched_by_jobs_controller'] = kwargs.pop(
            'is_launched_by_jobs_controller')
        kwargs['_is_launched_by_sky_serve_controller'] = kwargs.pop(
            'is_launched_by_sky_serve_controller')
        kwargs['_disable_controller_check'] = kwargs.pop(
            'disable_controller_check')
        return kwargs


class ExecBody(RequestBody):
    """The request body for the exec endpoint."""
    task: str
    cluster_name: str
    dryrun: bool = False
    down: bool = False
    backend: Optional[str] = None

    def to_kwargs(self) -> Dict[str, Any]:

        kwargs = super().to_kwargs()
        dag = common.process_mounts_in_task_on_api_server(self.task,
                                                          self.env_vars,
                                                          workdir_only=True)
        backend_cls = registry.BACKEND_REGISTRY.from_str(self.backend)
        backend = backend_cls() if backend_cls is not None else None
        kwargs['task'] = dag
        kwargs['backend'] = backend
        return kwargs


class StopOrDownBody(RequestBody):
    cluster_name: str
    purge: bool = False


class StatusBody(RequestBody):
    """The request body for the status endpoint."""
    cluster_names: Optional[List[str]] = None
    refresh: common_lib.StatusRefreshMode = common_lib.StatusRefreshMode.NONE
    all_users: bool = True


class StartBody(RequestBody):
    """The request body for the start endpoint."""
    cluster_name: str
    idle_minutes_to_autostop: Optional[int] = None
    retry_until_up: bool = False
    down: bool = False
    force: bool = False


class AutostopBody(RequestBody):
    """The request body for the autostop endpoint."""
    cluster_name: str
    idle_minutes: int
    down: bool = False


class QueueBody(RequestBody):
    """The request body for the queue endpoint."""
    cluster_name: str
    skip_finished: bool = False
    all_users: bool = False


class CancelBody(RequestBody):
    """The request body for the cancel endpoint."""
    cluster_name: str
    job_ids: Optional[List[int]]
    all: bool = False
    all_users: bool = False
    # Internal only. We cannot use prefix `_` because pydantic will not
    # include it in the request body.
    try_cancel_if_cluster_is_init: bool = False

    def to_kwargs(self) -> Dict[str, Any]:
        kwargs = super().to_kwargs()
        kwargs['_try_cancel_if_cluster_is_init'] = kwargs.pop(
            'try_cancel_if_cluster_is_init')
        return kwargs


class ClusterNameBody(RequestBody):
    """Cluster node."""
    cluster_name: str


class ClusterJobBody(RequestBody):
    """The request body for the cluster job endpoint."""
    cluster_name: str
    job_id: Optional[int]
    follow: bool = True
    tail: int = 0


class ClusterJobsBody(RequestBody):
    """The request body for the cluster jobs endpoint."""
    cluster_name: str
    job_ids: Optional[List[str]]


class ClusterJobsDownloadLogsBody(RequestBody):
    """The request body for the cluster jobs download logs endpoint."""
    cluster_name: str
    job_ids: Optional[List[str]]
    local_dir: str = constants.SKY_LOGS_DIRECTORY


class DownloadBody(RequestBody):
    """The request body for the download endpoint."""
    folder_paths: List[str]


class StorageBody(RequestBody):
    """The request body for the storage endpoint."""
    name: str


class EndpointsBody(RequestBody):
    """The request body for the endpoint."""
    cluster: str
    port: Optional[Union[int, str]] = None


class ServeEndpointBody(RequestBody):
    """The request body for the serve controller endpoint."""
    port: Optional[Union[int, str]] = None


class JobStatusBody(RequestBody):
    """The request body for the job status endpoint."""
    cluster_name: str
    job_ids: Optional[List[int]]


class JobsLaunchBody(RequestBody):
    """The request body for the jobs launch endpoint."""
    task: str
    name: Optional[str]

    def to_kwargs(self) -> Dict[str, Any]:
        kwargs = super().to_kwargs()
        kwargs['task'] = common.process_mounts_in_task_on_api_server(
            self.task, self.env_vars, workdir_only=False)
        return kwargs


class JobsQueueBody(RequestBody):
    """The request body for the jobs queue endpoint."""
    refresh: bool = False
    skip_finished: bool = False
    all_users: bool = False


class JobsCancelBody(RequestBody):
    """The request body for the jobs cancel endpoint."""
    name: Optional[str] = None
    job_ids: Optional[List[int]] = None
    all: bool = False
    all_users: bool = False


class JobsLogsBody(RequestBody):
    """The request body for the jobs logs endpoint."""
    name: Optional[str] = None
    job_id: Optional[int] = None
    follow: bool = True
    controller: bool = False
    refresh: bool = False


class RequestCancelBody(RequestBody):
    """The request body for the API request cancellation endpoint."""
    # Kill all requests if request_ids is None.
    request_ids: Optional[List[str]] = None
    user_id: Optional[str] = None


class RequestStatusBody(pydantic.BaseModel):
    """The request body for the API request status endpoint."""
    request_ids: Optional[List[str]] = None
    all_status: bool = False


class ServeUpBody(RequestBody):
    """The request body for the serve up endpoint."""
    task: str
    service_name: str

    def to_kwargs(self) -> Dict[str, Any]:
        kwargs = super().to_kwargs()
        dag = common.process_mounts_in_task_on_api_server(self.task,
                                                          self.env_vars,
                                                          workdir_only=False)
        assert len(
            dag.tasks) == 1, ('Must only specify one task in the DAG for '
                              'a service.', dag)
        kwargs['task'] = dag.tasks[0]
        return kwargs


class ServeUpdateBody(RequestBody):
    """The request body for the serve update endpoint."""
    task: str
    service_name: str
    mode: serve.UpdateMode

    def to_kwargs(self) -> Dict[str, Any]:
        kwargs = super().to_kwargs()
        dag = common.process_mounts_in_task_on_api_server(self.task,
                                                          self.env_vars,
                                                          workdir_only=False)
        assert len(
            dag.tasks) == 1, ('Must only specify one task in the DAG for '
                              'a service.', dag)
        kwargs['task'] = dag.tasks[0]
        return kwargs


class ServeDownBody(RequestBody):
    """The request body for the serve down endpoint."""
    service_names: Optional[Union[str, List[str]]]
    all: bool = False
    purge: bool = False


class ServeLogsBody(RequestBody):
    """The request body for the serve logs endpoint."""
    service_name: str
    target: Union[str, serve.ServiceComponent]
    replica_id: Optional[int] = None
    follow: bool = True


class ServeStatusBody(RequestBody):
    """The request body for the serve status endpoint."""
    service_names: Optional[Union[str, List[str]]]


class RealtimeGpuAvailabilityRequestBody(RequestBody):
    """The request body for the realtime GPU availability endpoint."""
    context: Optional[str]
    name_filter: Optional[str]
    quantity_filter: Optional[int]


class KubernetesNodeInfoRequestBody(RequestBody):
    """The request body for the kubernetes node info endpoint."""
    context: Optional[str] = None


class ListAcceleratorsBody(RequestBody):
    """The request body for the list accelerators endpoint."""
    gpus_only: bool = True
    name_filter: Optional[str] = None
    region_filter: Optional[str] = None
    quantity_filter: Optional[int] = None
    clouds: Optional[Union[List[str], str]] = None
    all_regions: bool = False
    require_price: bool = True
    case_sensitive: bool = True


class ListAcceleratorCountsBody(RequestBody):
    """The request body for the list accelerator counts endpoint."""
    gpus_only: bool = True
    name_filter: Optional[str] = None
    region_filter: Optional[str] = None
    quantity_filter: Optional[int] = None
    clouds: Optional[Union[List[str], str]] = None


class LocalUpBody(RequestBody):
    """The request body for the local up endpoint."""
    gpus: bool = True
    ips: Optional[List[str]] = None
    ssh_user: Optional[str] = None
    ssh_key: Optional[str] = None
    cleanup: bool = False
    context_name: Optional[str] = None
    password: Optional[str] = None


class ServeTerminateReplicaBody(RequestBody):
    """The request body for the serve terminate replica endpoint."""
    service_name: str
    replica_id: int
    purge: bool = False


class KillRequestProcessesBody(RequestBody):
    """The request body for the kill request processes endpoint."""
    request_ids: List[str]


class StreamBody(pydantic.BaseModel):
    """The request body for the stream endpoint."""
    request_id: Optional[str] = None
    log_path: Optional[str] = None
    tail: Optional[int] = None
    plain_logs: bool = True


class JobsDownloadLogsBody(RequestBody):
    """The request body for the jobs download logs endpoint."""
    name: Optional[str]
    job_id: Optional[int]
    refresh: bool = False
    controller: bool = False
    local_dir: str = constants.SKY_LOGS_DIRECTORY


class UploadZipFileResponse(pydantic.BaseModel):
    """The response body for the upload zip file endpoint."""
    status: str
    missing_chunks: Optional[List[str]] = None
