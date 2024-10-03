"""Payloads for the Sky API requests."""
import functools
import os
import tempfile
from typing import Any, Dict, List, Optional, Tuple, Union

import pydantic

from sky import serve
from sky.skylet import constants
from sky.utils import common
from sky.utils import common_utils


@functools.lru_cache()
def request_body_env_vars() -> dict:
    env_vars = {}
    for env_var in os.environ:
        if env_var.startswith('SKYPILOT_'):
            env_vars[env_var] = os.environ[env_var]
    env_vars[constants.USER_ID_ENV_VAR] = common_utils.get_user_hash()
    return env_vars


class RequestBody(pydantic.BaseModel):
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
    clouds: Optional[Tuple[str]]
    verbose: bool


class OptimizeBody(RequestBody):
    dag: str
    minimize: common.OptimizeTarget = common.OptimizeTarget.COST

    def to_kwargs(self) -> Dict[str, Any]:
        # Import here to avoid requirement of the whole SkyPilot dependency on
        # local clients.
        from sky.utils import (
            dag_utils)  # pylint: disable=import-outside-toplevel

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
    optimize_target: common.OptimizeTarget = common.OptimizeTarget.COST
    no_setup: bool = False
    clone_disk_from: Optional[str] = None
    # Internal only:
    # pylint: disable=invalid-name
    quiet_optimizer: bool = False
    is_launched_by_jobs_controller: bool = False
    is_launched_by_sky_serve_controller: bool = False
    disable_controller_check: bool = False

    def to_kwargs(self) -> Dict[str, Any]:
        from sky.api import common
        from sky.utils import registry
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
    task: str
    cluster_name: str
    dryrun: bool = False
    down: bool = False
    backend: Optional[str] = None

    def to_kwargs(self) -> Dict[str, Any]:
        from sky.api import common
        from sky.utils import registry

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
    cluster_names: Optional[List[str]] = None
    refresh: common.StatusRefreshMode = common.StatusRefreshMode.NONE


class StartBody(RequestBody):
    cluster_name: str
    idle_minutes_to_autostop: Optional[int] = None
    retry_until_up: bool = False
    down: bool = False
    force: bool = False


class AutostopBody(RequestBody):
    cluster_name: str
    idle_minutes: int
    down: bool = False


class QueueBody(RequestBody):
    cluster_name: str
    skip_finished: bool = False
    all_users: bool = False


class CancelBody(RequestBody):
    cluster_name: str
    job_ids: Optional[List[int]]
    all: bool = False
    # Internal only:
    try_cancel_if_cluster_is_init: bool = False


class ClusterJobBody(RequestBody):
    cluster_name: str
    job_id: Optional[int]
    follow: bool = True


class ClusterJobsBody(RequestBody):
    cluster_name: str
    job_ids: Optional[List[int]]


class StorageBody(RequestBody):
    name: str


class RequestIdBody(RequestBody):
    request_id: Optional[str]


class RequestLsBody(RequestBody):
    request_id: Optional[str] = None


class EndpointBody(RequestBody):
    cluster_name: str
    port: Optional[Union[int, str]] = None


class JobStatusBody(RequestBody):
    cluster_name: str
    job_ids: Optional[List[int]]


class JobsLaunchBody(RequestBody):
    task: str
    name: Optional[str]
    retry_until_up: bool

    def to_kwargs(self) -> Dict[str, Any]:
        from sky.api import common
        kwargs = super().to_kwargs()
        kwargs['task'] = common.process_mounts_in_task(self.task,
                                                       self.env_vars,
                                                       workdir_only=False)
        return kwargs


class JobsQueueBody(RequestBody):
    refresh: bool = False
    skip_finished: bool = False


class JobsCancelBody(RequestBody):
    name: Optional[str]
    job_ids: Optional[List[int]]
    all: bool = False


class JobsLogsBody(RequestBody):
    name: Optional[str]
    job_id: Optional[int]
    follow: bool = True
    controller: bool = False


class ServeUpBody(RequestBody):
    task: str
    service_name: str

    def to_kwargs(self) -> Dict[str, Any]:
        from sky.api import common
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
    task: str
    service_name: str
    mode: serve.UpdateMode

    def to_kwargs(self) -> Dict[str, Any]:
        from sky.api import common
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
    service_names: Optional[Union[str, List[str]]]
    all: bool = False
    purge: bool = False


class ServeLogsBody(RequestBody):
    service_name: str
    target: Union[str, serve.ServiceComponent]
    replica_id: Optional[int] = None
    follow: bool = True


class ServeStatusBody(RequestBody):
    service_names: Optional[Union[str, List[str]]]


class RealtimeGpuAvailabilityRequestBody(RequestBody):
    context: Optional[str]
    name_filter: Optional[str]
    quantity_filter: Optional[int]


class ListAcceleratorsBody(RequestBody):
    gpus_only: bool = True
    name_filter: Optional[str] = None
    region_filter: Optional[str] = None
    quantity_filter: Optional[int] = None
    clouds: Optional[Union[List[str], str]] = None
    all_regions: bool = False
    require_price: bool = True
    case_sensitive: bool = True


class LocalUpBody(RequestBody):
    gpus: bool = True


class KillChildrenProcessesBody(RequestBody):
    parent_pids: List[int]
    force: bool = False
