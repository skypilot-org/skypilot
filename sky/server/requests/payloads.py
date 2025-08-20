"""Payloads for the Sky API requests.

All the payloads that will be used between the client and server communication
must be defined here to make sure it get covered by our API compatbility tests.

Compatibility note:
- Adding a new body for new API is compatible as long as the SDK method using
  the new API is properly decorated with `versions.minimal_api_version`.
- Adding a new field with default value to an existing body is compatible at
  API level, but the business logic must handle the case where the field is
  not proccessed by an old version of remote client/server. This can usually
  be done by checking `versions.get_remote_api_version()`.
- Other changes are not compatible at API level, so must be handled specially.
  A common pattern is to keep both the old and new version of the body and
  checking `versions.get_remote_api_version()` to decide which body to use. For
  example, say we refactor the `LaunchBody`, the original `LaunchBody` must be
  kept in the codebase and the new body should be added via `LaunchBodyV2`.
  Then if the remote runs in an old version, the local code should still send
  `LaunchBody` to keep the backward compatibility. `LaunchBody` can be removed
  later when constants.MIN_COMPATIBLE_API_VERSION is updated to a version that
  supports `LaunchBodyV2`

Also refer to sky.server.constants.MIN_COMPATIBLE_API_VERSION and the
sky.server.versions module for more details.
"""
import os
import typing
from typing import Any, Dict, List, Optional, Tuple, Union

from sky import admin_policy
from sky import serve
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import common as adaptors_common
from sky.server import common
from sky.skylet import autostop_lib
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

# These non-skypilot environment variables will be updated from the local
# environment on each request when running a local API server.
# We should avoid adding variables here, but we should include credential-
# related variables.
EXTERNAL_LOCAL_ENV_VARS = [
    # Allow overriding the AWS authentication.
    'AWS_PROFILE',
    'AWS_DEFAULT_PROFILE',
    'AWS_ACCESS_KEY_ID',
    'AWS_SECRET_ACCESS_KEY',
    'AWS_SESSION_TOKEN',
    # Allow overriding the GCP authentication.
    'GOOGLE_APPLICATION_CREDENTIALS',
    # Allow overriding the kubeconfig.
    'KUBECONFIG',
]


@annotations.lru_cache(scope='global')
def request_body_env_vars() -> dict:
    env_vars = {}
    for env_var in os.environ:
        if env_var.startswith(constants.SKYPILOT_ENV_VAR_PREFIX):
            env_vars[env_var] = os.environ[env_var]
        if common.is_api_server_local() and env_var in EXTERNAL_LOCAL_ENV_VARS:
            env_vars[env_var] = os.environ[env_var]
    env_vars[constants.USER_ID_ENV_VAR] = common_utils.get_user_hash()
    env_vars[constants.USER_ENV_VAR] = common_utils.get_current_user_name()
    env_vars[
        usage_constants.USAGE_RUN_ID_ENV_VAR] = usage_lib.messages.usage.run_id
    # Remove the path to config file, as the config content is included in the
    # request body and will be merged with the config on the server side.
    env_vars.pop(skypilot_config.ENV_VAR_SKYPILOT_CONFIG, None)
    env_vars.pop(skypilot_config.ENV_VAR_GLOBAL_CONFIG, None)
    env_vars.pop(skypilot_config.ENV_VAR_PROJECT_CONFIG, None)
    return env_vars


def get_override_skypilot_config_from_client() -> Dict[str, Any]:
    """Returns the override configs from the client."""
    if annotations.is_on_api_server:
        return {}
    config = skypilot_config.to_dict()
    # Remove the API server config, as we should not specify the SkyPilot
    # server endpoint on the server side. This avoids the warning at
    # server-side.
    config.pop_nested(('api_server',), default_value=None)
    # Remove the admin policy, as the policy has been applied on the client
    # side.
    config.pop_nested(('admin_policy',), default_value=None)
    return config


def get_override_skypilot_config_path_from_client() -> Optional[str]:
    """Returns the override config path from the client."""
    if annotations.is_on_api_server:
        return None
    # Currently, we don't need to check if the client-side config
    # has been overridden because we only deal with cases where
    # client has a project-level config/changed config and the
    # api server has a different config.
    return skypilot_config.loaded_config_path_serialized()


class BasePayload(pydantic.BaseModel):
    """The base payload for the SkyPilot API."""
    # Ignore extra fields in the request body, which is useful for backward
    # compatibility. The difference with `allow` is that `ignore` will not
    # include the unknown fields when dump the model, i.e., we can add new
    # fields to the request body without breaking the existing old API server
    # where the handler function does not accept the new field in function
    # signature.
    model_config = pydantic.ConfigDict(extra='ignore')


class RequestBody(BasePayload):
    """The request body for the SkyPilot API."""
    env_vars: Dict[str, str] = {}
    entrypoint: str = ''
    entrypoint_command: str = ''
    using_remote_api_server: bool = False
    override_skypilot_config: Optional[Dict[str, Any]] = {}
    override_skypilot_config_path: Optional[str] = None

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
        data['override_skypilot_config_path'] = data.get(
            'override_skypilot_config_path',
            get_override_skypilot_config_path_from_client())
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
        kwargs.pop('override_skypilot_config_path')
        return kwargs

    @property
    def user_hash(self) -> Optional[str]:
        return self.env_vars.get(constants.USER_ID_ENV_VAR)


class CheckBody(RequestBody):
    """The request body for the check endpoint."""
    clouds: Optional[Tuple[str, ...]] = None
    verbose: bool = False
    workspace: Optional[str] = None


class EnabledCloudsBody(RequestBody):
    """The request body for the enabled clouds endpoint."""
    workspace: Optional[str] = None
    expand: bool = False


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


class DagRequestBodyWithRequestOptions(DagRequestBody):
    """Request body base class for endpoints with a dag and request options."""
    request_options: Optional[admin_policy.RequestOptions]

    def get_request_options(self) -> Optional[admin_policy.RequestOptions]:
        """Get the request options."""
        if self.request_options is None:
            return None
        if isinstance(self.request_options, dict):
            return admin_policy.RequestOptions(**self.request_options)
        return self.request_options

    def to_kwargs(self) -> Dict[str, Any]:
        kwargs = super().to_kwargs()
        kwargs['request_options'] = self.get_request_options()
        return kwargs


class ValidateBody(DagRequestBodyWithRequestOptions):
    """The request body for the validate endpoint."""
    dag: str


class OptimizeBody(DagRequestBodyWithRequestOptions):
    """The request body for the optimize endpoint."""
    dag: str
    minimize: common_lib.OptimizeTarget = common_lib.OptimizeTarget.COST


class LaunchBody(RequestBody):
    """The request body for the launch endpoint."""
    task: str
    cluster_name: str
    retry_until_up: bool = False
    # TODO(aylei): remove this field in v0.12.0
    idle_minutes_to_autostop: Optional[int] = None
    dryrun: bool = False
    # TODO(aylei): remove this field in v0.12.0
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
    wait_for: Optional[autostop_lib.AutostopWaitFor] = None
    retry_until_up: bool = False
    down: bool = False
    force: bool = False


class AutostopBody(RequestBody):
    """The request body for the autostop endpoint."""
    cluster_name: str
    idle_minutes: int
    wait_for: Optional[autostop_lib.AutostopWaitFor] = None
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


class UserCreateBody(RequestBody):
    """The request body for the user create endpoint."""
    username: str
    password: str
    role: Optional[str] = None


class UserDeleteBody(RequestBody):
    """The request body for the user delete endpoint."""
    user_id: str


class UserUpdateBody(RequestBody):
    """The request body for the user update endpoint."""
    user_id: str
    role: Optional[str] = None
    password: Optional[str] = None


class UserImportBody(RequestBody):
    """The request body for the user import endpoint."""
    csv_content: str


class ServiceAccountTokenCreateBody(RequestBody):
    """The request body for creating a service account token."""
    token_name: str
    expires_in_days: Optional[int] = None


class ServiceAccountTokenDeleteBody(RequestBody):
    """The request body for deleting a service account token."""
    token_id: str


class UpdateRoleBody(RequestBody):
    """The request body for updating a user role."""
    role: str


class ServiceAccountTokenRoleBody(RequestBody):
    """The request body for getting a service account token role."""
    token_id: str


class ServiceAccountTokenUpdateRoleBody(RequestBody):
    """The request body for updating a service account token role."""
    token_id: str
    role: str


class ServiceAccountTokenRotateBody(RequestBody):
    """The request body for rotating a service account token."""
    token_id: str
    expires_in_days: Optional[int] = None


class DownloadBody(RequestBody):
    """The request body for the download endpoint."""
    folder_paths: List[str]


class StorageBody(RequestBody):
    """The request body for the storage endpoint."""
    name: str


class VolumeApplyBody(RequestBody):
    """The request body for the volume apply endpoint."""
    name: str
    volume_type: str
    cloud: str
    region: Optional[str] = None
    zone: Optional[str] = None
    size: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    labels: Optional[Dict[str, str]] = None


class VolumeDeleteBody(RequestBody):
    """The request body for the volume delete endpoint."""
    names: List[str]


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
    pool: Optional[str] = None
    num_jobs: Optional[int] = None

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
    job_ids: Optional[List[int]] = None
    user_match: Optional[str] = None
    workspace_match: Optional[str] = None
    name_match: Optional[str] = None
    pool_match: Optional[str] = None
    page: Optional[int] = None
    limit: Optional[int] = None
    statuses: Optional[List[str]] = None


class JobsCancelBody(RequestBody):
    """The request body for the jobs cancel endpoint."""
    name: Optional[str] = None
    job_ids: Optional[List[int]] = None
    all: bool = False
    all_users: bool = False
    pool: Optional[str] = None


class JobsLogsBody(RequestBody):
    """The request body for the jobs logs endpoint."""
    name: Optional[str] = None
    job_id: Optional[int] = None
    follow: bool = True
    controller: bool = False
    refresh: bool = False
    tail: Optional[int] = None


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
    tail: Optional[int] = None


class ServeDownloadLogsBody(RequestBody):
    """The request body for the serve download logs endpoint."""
    service_name: str
    local_dir: str
    targets: Optional[Union[str, serve.ServiceComponent,
                            List[Union[str, serve.ServiceComponent]]]]
    replica_ids: Optional[List[int]] = None
    tail: Optional[int] = None


class ServeStatusBody(RequestBody):
    """The request body for the serve status endpoint."""
    service_names: Optional[Union[str, List[str]]]


class RealtimeGpuAvailabilityRequestBody(RequestBody):
    """The request body for the realtime GPU availability endpoint."""
    context: Optional[str] = None
    name_filter: Optional[str] = None
    quantity_filter: Optional[int] = None
    is_ssh: Optional[bool] = None


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


class SSHUpBody(RequestBody):
    """The request body for the SSH up/down endpoints."""
    infra: Optional[str] = None
    cleanup: bool = False


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


class JobsPoolApplyBody(RequestBody):
    """The request body for the jobs pool apply endpoint."""
    task: str
    pool_name: str
    mode: serve.UpdateMode

    def to_kwargs(self) -> Dict[str, Any]:
        kwargs = super().to_kwargs()
        dag = common.process_mounts_in_task_on_api_server(self.task,
                                                          self.env_vars,
                                                          workdir_only=False)
        assert len(
            dag.tasks) == 1, ('Must only specify one task in the DAG for '
                              'a pool.', dag)
        kwargs['task'] = dag.tasks[0]
        return kwargs


class JobsPoolDownBody(RequestBody):
    """The request body for the jobs pool down endpoint."""
    pool_names: Optional[Union[str, List[str]]]
    all: bool = False
    purge: bool = False


class JobsPoolStatusBody(RequestBody):
    """The request body for the jobs pool status endpoint."""
    pool_names: Optional[Union[str, List[str]]]


class JobsPoolLogsBody(RequestBody):
    """The request body for the jobs pool logs endpoint."""
    pool_name: str
    target: Union[str, serve.ServiceComponent]
    worker_id: Optional[int] = None
    follow: bool = True
    tail: Optional[int] = None


class JobsPoolDownloadLogsBody(RequestBody):
    """The request body for the jobs pool download logs endpoint."""
    pool_name: str
    local_dir: str
    targets: Optional[Union[str, serve.ServiceComponent,
                            List[Union[str, serve.ServiceComponent]]]]
    worker_ids: Optional[List[int]] = None
    tail: Optional[int] = None


class UploadZipFileResponse(pydantic.BaseModel):
    """The response body for the upload zip file endpoint."""
    status: str
    missing_chunks: Optional[List[str]] = None


class UpdateWorkspaceBody(RequestBody):
    """The request body for updating a specific workspace configuration."""
    workspace_name: str = ''  # Will be set from path parameter
    config: Dict[str, Any]


class CreateWorkspaceBody(RequestBody):
    """The request body for creating a new workspace."""
    workspace_name: str = ''  # Will be set from path parameter
    config: Dict[str, Any]


class DeleteWorkspaceBody(RequestBody):
    """The request body for deleting a workspace."""
    workspace_name: str


class UpdateConfigBody(RequestBody):
    """The request body for updating the entire SkyPilot configuration."""
    config: Dict[str, Any]


class GetConfigBody(RequestBody):
    """The request body for getting the entire SkyPilot configuration."""
    pass


class CostReportBody(RequestBody):
    """The request body for the cost report endpoint."""
    days: Optional[int] = 30


class RequestPayload(BasePayload):
    """The payload for the requests."""

    request_id: str
    name: str
    entrypoint: str
    request_body: str
    status: str
    created_at: float
    user_id: str
    return_value: str
    error: str
    pid: Optional[int]
    schedule_type: str
    user_name: Optional[str] = None
    # Resources the request operates on.
    cluster_name: Optional[str] = None
    status_msg: Optional[str] = None
    should_retry: bool = False
    finished_at: Optional[float] = None
