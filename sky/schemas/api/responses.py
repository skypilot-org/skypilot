"""Responses for the API server."""

import enum
from typing import Any, Dict, List, Optional

import pydantic

from sky import data
from sky import models
from sky.jobs import state as job_state
from sky.server import common
from sky.skylet import job_lib
from sky.utils import status_lib


class ResponseBaseModel(pydantic.BaseModel):
    """A pydantic model that acts like a dict.

    Supports the following syntax:
    class SampleResponse(DictLikePayload):
        field: str

    response = SampleResponse(field='value')
    print(response['field']) # prints 'value'
    response['field'] = 'value2'
    print(response['field']) # prints 'value2'
    print('field' in response) # prints True

    This model exists for backwards compatibility with the
    old SDK that used to return a dict.

    The backward compatibility may be removed
    in the future.
    """
    # Ignore extra fields in the request body, which is useful for backward
    # compatibility. The difference with `allow` is that `ignore` will not
    # include the unknown fields when dump the model, i.e., we can add new
    # fields to the request body without breaking the existing old API server
    # where the handler function does not accept the new field in function
    # signature.
    model_config = pydantic.ConfigDict(extra='ignore')

    # backward compatibility with dict
    # TODO(syang): remove this in v0.13.0
    def __getitem__(self, key):
        try:
            return getattr(self, key)
        except AttributeError as e:
            raise KeyError(key) from e

    def __setitem__(self, key, value):
        setattr(self, key, value)

    def get(self, key, default=None):
        return getattr(self, key, default)

    def __contains__(self, key):
        return hasattr(self, key)

    def keys(self):
        return self.model_dump().keys()

    def values(self):
        return self.model_dump().values()

    def items(self):
        return self.model_dump().items()

    def __repr__(self):
        return self.__dict__.__repr__()


class APIHealthResponse(ResponseBaseModel):
    """Response for the API health endpoint."""
    status: common.ApiServerStatus
    api_version: str = ''
    version: str = ''
    version_on_disk: str = ''
    commit: str = ''
    # Whether basic auth on api server is enabled
    basic_auth_enabled: bool = False
    user: Optional[models.User] = None
    # Whether service account token is enabled
    service_account_token_enabled: bool = False
    # Whether basic auth on ingress is enabled
    ingress_basic_auth_enabled: bool = False
    # Latest version info (if available)
    latest_version: Optional[str] = None
    # Whether external proxy auth is enabled
    external_proxy_auth_enabled: bool = False


class StatusResponse(ResponseBaseModel):
    """Response for the status endpoint."""
    name: str
    launched_at: int
    # pydantic cannot generate the pydantic-core schema for
    # backends.ResourceHandle, so we use Any here.
    # This is an internally facing field anyway, so it's less
    # of a problem that it's not typed.
    handle: Optional[Any] = None
    last_use: Optional[str] = None
    status: status_lib.ClusterStatus
    autostop: int
    to_down: bool
    owner: Optional[List[str]] = None
    # metadata is a JSON, so we use Any here.
    metadata: Optional[Dict[str, Any]] = None
    cluster_hash: str
    cluster_ever_up: bool
    status_updated_at: Optional[int] = None
    user_hash: str
    user_name: str
    config_hash: Optional[str] = None
    workspace: str
    last_creation_yaml: Optional[str] = None
    last_creation_command: Optional[str] = None
    is_managed: bool
    last_event: Optional[str] = None
    resources_str: Optional[str] = None
    resources_str_full: Optional[str] = None
    # credentials is a JSON, so we use Any here.
    credentials: Optional[Dict[str, Any]] = None
    nodes: int
    cloud: Optional[str] = None
    region: Optional[str] = None
    cpus: Optional[str] = None
    memory: Optional[str] = None
    accelerators: Optional[str] = None
    labels: Optional[Dict[str, str]] = None
    cluster_name_on_cloud: Optional[str] = None


class ClusterJobRecord(ResponseBaseModel):
    """Response for the cluster job queue endpoint."""
    job_id: int
    job_name: str
    username: str
    user_hash: str
    submitted_at: float
    # None if the job has not started yet.
    start_at: Optional[float] = None
    # None if the job has not ended yet.
    end_at: Optional[float] = None
    resources: str
    status: job_lib.JobStatus
    log_path: str
    metadata: Dict[str, Any] = {}


class UploadStatus(enum.Enum):
    """Status of the upload."""
    UPLOADING = 'uploading'
    COMPLETED = 'completed'


class StorageRecord(ResponseBaseModel):
    """Response for the storage list endpoint."""
    name: str
    launched_at: int
    store: List[data.StoreType]
    last_use: str
    status: status_lib.StorageStatus


# TODO (syang) figure out which fields are always present
# and therefore can be non-optional.
class ManagedJobRecord(ResponseBaseModel):
    """A single managed job record."""
    # The job_id in the spot table
    task_job_id: Optional[int] = pydantic.Field(None, alias='_job_id')
    job_id: Optional[int] = None
    task_id: Optional[int] = None
    job_name: Optional[str] = None
    task_name: Optional[str] = None
    job_duration: Optional[float] = None
    workspace: Optional[str] = None
    status: Optional[job_state.ManagedJobStatus] = None
    schedule_state: Optional[str] = None
    resources: Optional[str] = None
    cluster_resources: Optional[str] = None
    cluster_resources_full: Optional[str] = None
    cloud: Optional[str] = None
    region: Optional[str] = None
    zone: Optional[str] = None
    infra: Optional[str] = None
    recovery_count: Optional[int] = None
    details: Optional[str] = None
    failure_reason: Optional[str] = None
    user_name: Optional[str] = None
    user_hash: Optional[str] = None
    submitted_at: Optional[float] = None
    start_at: Optional[float] = None
    end_at: Optional[float] = None
    user_yaml: Optional[str] = None
    entrypoint: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    controller_pid: Optional[int] = None
    controller_pid_started_at: Optional[float] = None
    dag_yaml_path: Optional[str] = None
    env_file_path: Optional[str] = None
    last_recovered_at: Optional[float] = None
    run_timestamp: Optional[str] = None
    priority: Optional[int] = None
    original_user_yaml_path: Optional[str] = None
    pool: Optional[str] = None
    pool_hash: Optional[str] = None
    current_cluster_name: Optional[str] = None
    cluster_name_on_cloud: Optional[str] = None
    job_id_on_pool_cluster: Optional[int] = None
    accelerators: Optional[Dict[str, int]] = None
    labels: Optional[Dict[str, str]] = None
    links: Optional[Dict[str, str]] = None
    # JobGroup fields
    # Execution mode: 'parallel' (job group) or 'serial' (pipeline/single job)
    execution: Optional[str] = None
    is_job_group: Optional[bool] = None
    # Whether this task is a primary task (True) or auxiliary task (False)
    # within a job group. NULL for non-job-group jobs (single jobs and
    # pipelines).
    is_primary_in_job_group: Optional[bool] = None


class VolumeRecord(ResponseBaseModel):
    """A single volume record."""
    name: str
    type: str
    launched_at: int
    cloud: str
    region: Optional[str] = None
    zone: Optional[str] = None
    size: Optional[str] = None
    config: Dict[str, Any]
    name_on_cloud: str
    user_hash: str
    user_name: str
    workspace: str
    last_attached_at: Optional[int] = None
    last_use: Optional[str] = None
    status: Optional[str] = None
    usedby_pods: List[str]
    usedby_clusters: List[str]
    is_ephemeral: bool = False
    usedby_fetch_failed: bool = False
    # Error message for volume in ERROR state (e.g., PVC pending due to
    # access mode mismatch)
    error_message: Optional[str] = None
