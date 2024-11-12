"""Payloads for the Sky API requests."""
import functools
import os
import tempfile
from typing import Any, Dict, List, Optional, Tuple, Union

import pydantic

from sky import serve
from sky.api import common
from sky.skylet import constants
from sky.utils import common as common_lib
from sky.utils import common_utils
from sky.utils import registry


@functools.lru_cache()
def request_body_env_vars() -> dict:
    env_vars = {}
    for env_var in os.environ:
        if env_var.startswith('SKYPILOT_'):
            env_vars[env_var] = os.environ[env_var]
    env_vars[constants.USER_ID_ENV_VAR] = common_utils.get_user_hash()
    return env_vars


class RequestBody(pydantic.BaseModel):
    """The request body for the SkyPilot API."""
    env_vars: Dict[str, str] = request_body_env_vars()

    def to_kwargs(self) -> Dict[str, Any]:
        """Convert the request body to a kwargs dictionary on API server.

        This converts the request body into kwargs for the underlying SkyPilot
        backend's function.
        """
        kwargs = self.model_dump()
        kwargs.pop('env_vars')
        return kwargs


class CheckBody(RequestBody):
    """The request body for the check endpoint."""
    clouds: Optional[Tuple[str]]
    verbose: bool


class OptimizeBody(RequestBody):
    """The request body for the optimize endpoint."""
    dag: str
    minimize: common_lib.OptimizeTarget = common_lib.OptimizeTarget.COST

    def to_kwargs(self) -> Dict[str, Any]:
        # Import here to avoid requirement of the whole SkyPilot dependency on
        # local clients.
        # pylint: disable=import-outside-toplevel
        from sky.utils import dag_utils

        kwargs = super().to_kwargs()

        with tempfile.NamedTemporaryFile(mode='w') as f:
            f.write(self.dag)
            f.flush()
            dag = dag_utils.load_chain_dag_from_yaml(f.name)
        kwargs['dag'] = dag
        return kwargs


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
    # Internal only:
    # pylint: disable=invalid-name
    quiet_optimizer: bool = False
    is_launched_by_jobs_controller: bool = False
    is_launched_by_sky_serve_controller: bool = False
    disable_controller_check: bool = False

    def to_kwargs(self) -> Dict[str, Any]:

        kwargs = super().to_kwargs()
        dag = common.process_mounts_in_task(self.task,
                                            self.env_vars,
                                            workdir_only=False)

        backend = registry.BACKEND_REGISTRY.from_str(self.backend)
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
        dag = common.process_mounts_in_task(self.task,
                                            self.env_vars,
                                            workdir_only=True)
        backend = registry.BACKEND_REGISTRY.from_str(self.backend)
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
    # Internal only:
    try_cancel_if_cluster_is_init: bool = False


class ClusterNameBody(RequestBody):
    """Cluster node."""
    cluster_name: str


class ClusterJobBody(RequestBody):
    """The request body for the cluster job endpoint."""
    cluster_name: str
    job_id: Optional[int]
    follow: bool = True


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


class EndpointBody(RequestBody):
    """The request body for the endpoint."""
    cluster: str
    port: Optional[Union[int, str]] = None


class JobStatusBody(RequestBody):
    """The request body for the job status endpoint."""
    cluster_name: str
    job_ids: Optional[List[int]]


class JobsLaunchBody(RequestBody):
    """The request body for the jobs launch endpoint."""
    task: str
    name: Optional[str]
    retry_until_up: bool
    fast: bool = False

    def to_kwargs(self) -> Dict[str, Any]:
        kwargs = super().to_kwargs()
        kwargs['task'] = common.process_mounts_in_task(self.task,
                                                       self.env_vars,
                                                       workdir_only=False)
        return kwargs


class JobsQueueBody(RequestBody):
    """The request body for the jobs queue endpoint."""
    refresh: bool = False
    skip_finished: bool = False


class JobsCancelBody(RequestBody):
    """The request body for the jobs cancel endpoint."""
    name: Optional[str]
    job_ids: Optional[List[int]]
    all: bool = False


class JobsLogsBody(RequestBody):
    """The request body for the jobs logs endpoint."""
    name: Optional[str]
    job_id: Optional[int]
    follow: bool = True
    controller: bool = False


class RequestIdBody(RequestBody):
    """The request body for the API request endpoint."""
    request_id: Optional[str]
    all: bool = False


class ServeUpBody(RequestBody):
    """The request body for the serve up endpoint."""
    task: str
    service_name: str

    def to_kwargs(self) -> Dict[str, Any]:
        kwargs = super().to_kwargs()
        dag = common.process_mounts_in_task(self.task,
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
        dag = common.process_mounts_in_task(self.task,
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


class ServeTerminateReplicaBody(RequestBody):
    """The request body for the serve terminate replica endpoint."""
    service_name: str
    replica_id: int
    purge: bool = False


class KillRequestProcessesBody(RequestBody):
    """The request body for the kill request processes endpoint."""
    request_ids: List[str]
