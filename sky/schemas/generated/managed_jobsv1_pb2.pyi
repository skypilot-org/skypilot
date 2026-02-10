from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ManagedJobStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    MANAGED_JOB_STATUS_UNSPECIFIED: _ClassVar[ManagedJobStatus]
    MANAGED_JOB_STATUS_PENDING: _ClassVar[ManagedJobStatus]
    MANAGED_JOB_STATUS_SUBMITTED: _ClassVar[ManagedJobStatus]
    MANAGED_JOB_STATUS_STARTING: _ClassVar[ManagedJobStatus]
    MANAGED_JOB_STATUS_RUNNING: _ClassVar[ManagedJobStatus]
    MANAGED_JOB_STATUS_RECOVERING: _ClassVar[ManagedJobStatus]
    MANAGED_JOB_STATUS_CANCELLING: _ClassVar[ManagedJobStatus]
    MANAGED_JOB_STATUS_SUCCEEDED: _ClassVar[ManagedJobStatus]
    MANAGED_JOB_STATUS_CANCELLED: _ClassVar[ManagedJobStatus]
    MANAGED_JOB_STATUS_FAILED: _ClassVar[ManagedJobStatus]
    MANAGED_JOB_STATUS_FAILED_SETUP: _ClassVar[ManagedJobStatus]
    MANAGED_JOB_STATUS_FAILED_PRECHECKS: _ClassVar[ManagedJobStatus]
    MANAGED_JOB_STATUS_FAILED_NO_RESOURCE: _ClassVar[ManagedJobStatus]
    MANAGED_JOB_STATUS_FAILED_CONTROLLER: _ClassVar[ManagedJobStatus]

class ManagedJobScheduleState(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    MANAGED_JOB_SCHEDULE_STATE_UNSPECIFIED: _ClassVar[ManagedJobScheduleState]
    DEPRECATED_MANAGED_JOB_SCHEDULE_STATE_INVALID: _ClassVar[ManagedJobScheduleState]
    MANAGED_JOB_SCHEDULE_STATE_INACTIVE: _ClassVar[ManagedJobScheduleState]
    MANAGED_JOB_SCHEDULE_STATE_WAITING: _ClassVar[ManagedJobScheduleState]
    MANAGED_JOB_SCHEDULE_STATE_ALIVE_WAITING: _ClassVar[ManagedJobScheduleState]
    MANAGED_JOB_SCHEDULE_STATE_LAUNCHING: _ClassVar[ManagedJobScheduleState]
    MANAGED_JOB_SCHEDULE_STATE_ALIVE_BACKOFF: _ClassVar[ManagedJobScheduleState]
    MANAGED_JOB_SCHEDULE_STATE_ALIVE: _ClassVar[ManagedJobScheduleState]
    MANAGED_JOB_SCHEDULE_STATE_DONE: _ClassVar[ManagedJobScheduleState]
MANAGED_JOB_STATUS_UNSPECIFIED: ManagedJobStatus
MANAGED_JOB_STATUS_PENDING: ManagedJobStatus
MANAGED_JOB_STATUS_SUBMITTED: ManagedJobStatus
MANAGED_JOB_STATUS_STARTING: ManagedJobStatus
MANAGED_JOB_STATUS_RUNNING: ManagedJobStatus
MANAGED_JOB_STATUS_RECOVERING: ManagedJobStatus
MANAGED_JOB_STATUS_CANCELLING: ManagedJobStatus
MANAGED_JOB_STATUS_SUCCEEDED: ManagedJobStatus
MANAGED_JOB_STATUS_CANCELLED: ManagedJobStatus
MANAGED_JOB_STATUS_FAILED: ManagedJobStatus
MANAGED_JOB_STATUS_FAILED_SETUP: ManagedJobStatus
MANAGED_JOB_STATUS_FAILED_PRECHECKS: ManagedJobStatus
MANAGED_JOB_STATUS_FAILED_NO_RESOURCE: ManagedJobStatus
MANAGED_JOB_STATUS_FAILED_CONTROLLER: ManagedJobStatus
MANAGED_JOB_SCHEDULE_STATE_UNSPECIFIED: ManagedJobScheduleState
DEPRECATED_MANAGED_JOB_SCHEDULE_STATE_INVALID: ManagedJobScheduleState
MANAGED_JOB_SCHEDULE_STATE_INACTIVE: ManagedJobScheduleState
MANAGED_JOB_SCHEDULE_STATE_WAITING: ManagedJobScheduleState
MANAGED_JOB_SCHEDULE_STATE_ALIVE_WAITING: ManagedJobScheduleState
MANAGED_JOB_SCHEDULE_STATE_LAUNCHING: ManagedJobScheduleState
MANAGED_JOB_SCHEDULE_STATE_ALIVE_BACKOFF: ManagedJobScheduleState
MANAGED_JOB_SCHEDULE_STATE_ALIVE: ManagedJobScheduleState
MANAGED_JOB_SCHEDULE_STATE_DONE: ManagedJobScheduleState

class JobIds(_message.Message):
    __slots__ = ("ids",)
    IDS_FIELD_NUMBER: _ClassVar[int]
    ids: _containers.RepeatedScalarFieldContainer[int]
    def __init__(self, ids: _Optional[_Iterable[int]] = ...) -> None: ...

class UserHashes(_message.Message):
    __slots__ = ("hashes",)
    HASHES_FIELD_NUMBER: _ClassVar[int]
    hashes: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, hashes: _Optional[_Iterable[str]] = ...) -> None: ...

class Statuses(_message.Message):
    __slots__ = ("statuses",)
    STATUSES_FIELD_NUMBER: _ClassVar[int]
    statuses: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, statuses: _Optional[_Iterable[str]] = ...) -> None: ...

class Fields(_message.Message):
    __slots__ = ("fields",)
    FIELDS_FIELD_NUMBER: _ClassVar[int]
    fields: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, fields: _Optional[_Iterable[str]] = ...) -> None: ...

class Workspaces(_message.Message):
    __slots__ = ("workspaces",)
    WORKSPACES_FIELD_NUMBER: _ClassVar[int]
    workspaces: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, workspaces: _Optional[_Iterable[str]] = ...) -> None: ...

class GetVersionRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class GetVersionResponse(_message.Message):
    __slots__ = ("controller_version",)
    CONTROLLER_VERSION_FIELD_NUMBER: _ClassVar[int]
    controller_version: str
    def __init__(self, controller_version: _Optional[str] = ...) -> None: ...

class GetJobTableRequest(_message.Message):
    __slots__ = ("skip_finished", "accessible_workspaces", "job_ids", "workspace_match", "name_match", "pool_match", "page", "limit", "user_hashes", "statuses", "show_jobs_without_user_hash", "fields", "sort_by", "sort_order")
    SKIP_FINISHED_FIELD_NUMBER: _ClassVar[int]
    ACCESSIBLE_WORKSPACES_FIELD_NUMBER: _ClassVar[int]
    JOB_IDS_FIELD_NUMBER: _ClassVar[int]
    WORKSPACE_MATCH_FIELD_NUMBER: _ClassVar[int]
    NAME_MATCH_FIELD_NUMBER: _ClassVar[int]
    POOL_MATCH_FIELD_NUMBER: _ClassVar[int]
    PAGE_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    USER_HASHES_FIELD_NUMBER: _ClassVar[int]
    STATUSES_FIELD_NUMBER: _ClassVar[int]
    SHOW_JOBS_WITHOUT_USER_HASH_FIELD_NUMBER: _ClassVar[int]
    FIELDS_FIELD_NUMBER: _ClassVar[int]
    SORT_BY_FIELD_NUMBER: _ClassVar[int]
    SORT_ORDER_FIELD_NUMBER: _ClassVar[int]
    skip_finished: bool
    accessible_workspaces: Workspaces
    job_ids: JobIds
    workspace_match: str
    name_match: str
    pool_match: str
    page: int
    limit: int
    user_hashes: UserHashes
    statuses: Statuses
    show_jobs_without_user_hash: bool
    fields: Fields
    sort_by: str
    sort_order: str
    def __init__(self, skip_finished: bool = ..., accessible_workspaces: _Optional[_Union[Workspaces, _Mapping]] = ..., job_ids: _Optional[_Union[JobIds, _Mapping]] = ..., workspace_match: _Optional[str] = ..., name_match: _Optional[str] = ..., pool_match: _Optional[str] = ..., page: _Optional[int] = ..., limit: _Optional[int] = ..., user_hashes: _Optional[_Union[UserHashes, _Mapping]] = ..., statuses: _Optional[_Union[Statuses, _Mapping]] = ..., show_jobs_without_user_hash: bool = ..., fields: _Optional[_Union[Fields, _Mapping]] = ..., sort_by: _Optional[str] = ..., sort_order: _Optional[str] = ...) -> None: ...

class ManagedJobInfo(_message.Message):
    __slots__ = ("job_id", "task_id", "job_name", "task_name", "job_duration", "workspace", "status", "schedule_state", "resources", "cluster_resources", "cluster_resources_full", "cloud", "region", "infra", "accelerators", "recovery_count", "details", "failure_reason", "user_name", "user_hash", "submitted_at", "start_at", "end_at", "user_yaml", "entrypoint", "metadata", "pool", "pool_hash", "_job_id", "links", "is_primary_in_job_group", "zone", "labels", "cluster_name_on_cloud")
    class AcceleratorsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: float
        def __init__(self, key: _Optional[str] = ..., value: _Optional[float] = ...) -> None: ...
    class MetadataEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    class LinksEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    class LabelsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    JOB_NAME_FIELD_NUMBER: _ClassVar[int]
    TASK_NAME_FIELD_NUMBER: _ClassVar[int]
    JOB_DURATION_FIELD_NUMBER: _ClassVar[int]
    WORKSPACE_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    SCHEDULE_STATE_FIELD_NUMBER: _ClassVar[int]
    RESOURCES_FIELD_NUMBER: _ClassVar[int]
    CLUSTER_RESOURCES_FIELD_NUMBER: _ClassVar[int]
    CLUSTER_RESOURCES_FULL_FIELD_NUMBER: _ClassVar[int]
    CLOUD_FIELD_NUMBER: _ClassVar[int]
    REGION_FIELD_NUMBER: _ClassVar[int]
    INFRA_FIELD_NUMBER: _ClassVar[int]
    ACCELERATORS_FIELD_NUMBER: _ClassVar[int]
    RECOVERY_COUNT_FIELD_NUMBER: _ClassVar[int]
    DETAILS_FIELD_NUMBER: _ClassVar[int]
    FAILURE_REASON_FIELD_NUMBER: _ClassVar[int]
    USER_NAME_FIELD_NUMBER: _ClassVar[int]
    USER_HASH_FIELD_NUMBER: _ClassVar[int]
    SUBMITTED_AT_FIELD_NUMBER: _ClassVar[int]
    START_AT_FIELD_NUMBER: _ClassVar[int]
    END_AT_FIELD_NUMBER: _ClassVar[int]
    USER_YAML_FIELD_NUMBER: _ClassVar[int]
    ENTRYPOINT_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    POOL_FIELD_NUMBER: _ClassVar[int]
    POOL_HASH_FIELD_NUMBER: _ClassVar[int]
    _JOB_ID_FIELD_NUMBER: _ClassVar[int]
    LINKS_FIELD_NUMBER: _ClassVar[int]
    IS_PRIMARY_IN_JOB_GROUP_FIELD_NUMBER: _ClassVar[int]
    ZONE_FIELD_NUMBER: _ClassVar[int]
    LABELS_FIELD_NUMBER: _ClassVar[int]
    CLUSTER_NAME_ON_CLOUD_FIELD_NUMBER: _ClassVar[int]
    job_id: int
    task_id: int
    job_name: str
    task_name: str
    job_duration: float
    workspace: str
    status: ManagedJobStatus
    schedule_state: ManagedJobScheduleState
    resources: str
    cluster_resources: str
    cluster_resources_full: str
    cloud: str
    region: str
    infra: str
    accelerators: _containers.ScalarMap[str, float]
    recovery_count: int
    details: str
    failure_reason: str
    user_name: str
    user_hash: str
    submitted_at: float
    start_at: float
    end_at: float
    user_yaml: str
    entrypoint: str
    metadata: _containers.ScalarMap[str, str]
    pool: str
    pool_hash: str
    _job_id: int
    links: _containers.ScalarMap[str, str]
    is_primary_in_job_group: bool
    zone: str
    labels: _containers.ScalarMap[str, str]
    cluster_name_on_cloud: str
    def __init__(self, job_id: _Optional[int] = ..., task_id: _Optional[int] = ..., job_name: _Optional[str] = ..., task_name: _Optional[str] = ..., job_duration: _Optional[float] = ..., workspace: _Optional[str] = ..., status: _Optional[_Union[ManagedJobStatus, str]] = ..., schedule_state: _Optional[_Union[ManagedJobScheduleState, str]] = ..., resources: _Optional[str] = ..., cluster_resources: _Optional[str] = ..., cluster_resources_full: _Optional[str] = ..., cloud: _Optional[str] = ..., region: _Optional[str] = ..., infra: _Optional[str] = ..., accelerators: _Optional[_Mapping[str, float]] = ..., recovery_count: _Optional[int] = ..., details: _Optional[str] = ..., failure_reason: _Optional[str] = ..., user_name: _Optional[str] = ..., user_hash: _Optional[str] = ..., submitted_at: _Optional[float] = ..., start_at: _Optional[float] = ..., end_at: _Optional[float] = ..., user_yaml: _Optional[str] = ..., entrypoint: _Optional[str] = ..., metadata: _Optional[_Mapping[str, str]] = ..., pool: _Optional[str] = ..., pool_hash: _Optional[str] = ..., _job_id: _Optional[int] = ..., links: _Optional[_Mapping[str, str]] = ..., is_primary_in_job_group: bool = ..., zone: _Optional[str] = ..., labels: _Optional[_Mapping[str, str]] = ..., cluster_name_on_cloud: _Optional[str] = ...) -> None: ...

class GetJobTableResponse(_message.Message):
    __slots__ = ("jobs", "total", "total_no_filter", "status_counts")
    class StatusCountsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: int
        def __init__(self, key: _Optional[str] = ..., value: _Optional[int] = ...) -> None: ...
    JOBS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_FIELD_NUMBER: _ClassVar[int]
    TOTAL_NO_FILTER_FIELD_NUMBER: _ClassVar[int]
    STATUS_COUNTS_FIELD_NUMBER: _ClassVar[int]
    jobs: _containers.RepeatedCompositeFieldContainer[ManagedJobInfo]
    total: int
    total_no_filter: int
    status_counts: _containers.ScalarMap[str, int]
    def __init__(self, jobs: _Optional[_Iterable[_Union[ManagedJobInfo, _Mapping]]] = ..., total: _Optional[int] = ..., total_no_filter: _Optional[int] = ..., status_counts: _Optional[_Mapping[str, int]] = ...) -> None: ...

class GetAllJobIdsByNameRequest(_message.Message):
    __slots__ = ("job_name",)
    JOB_NAME_FIELD_NUMBER: _ClassVar[int]
    job_name: str
    def __init__(self, job_name: _Optional[str] = ...) -> None: ...

class GetAllJobIdsByNameResponse(_message.Message):
    __slots__ = ("job_ids",)
    JOB_IDS_FIELD_NUMBER: _ClassVar[int]
    job_ids: _containers.RepeatedScalarFieldContainer[int]
    def __init__(self, job_ids: _Optional[_Iterable[int]] = ...) -> None: ...

class CancelJobsRequest(_message.Message):
    __slots__ = ("current_workspace", "user_hash", "all_users", "job_ids", "job_name", "pool_name")
    CURRENT_WORKSPACE_FIELD_NUMBER: _ClassVar[int]
    USER_HASH_FIELD_NUMBER: _ClassVar[int]
    ALL_USERS_FIELD_NUMBER: _ClassVar[int]
    JOB_IDS_FIELD_NUMBER: _ClassVar[int]
    JOB_NAME_FIELD_NUMBER: _ClassVar[int]
    POOL_NAME_FIELD_NUMBER: _ClassVar[int]
    current_workspace: str
    user_hash: str
    all_users: bool
    job_ids: JobIds
    job_name: str
    pool_name: str
    def __init__(self, current_workspace: _Optional[str] = ..., user_hash: _Optional[str] = ..., all_users: bool = ..., job_ids: _Optional[_Union[JobIds, _Mapping]] = ..., job_name: _Optional[str] = ..., pool_name: _Optional[str] = ...) -> None: ...

class CancelJobsResponse(_message.Message):
    __slots__ = ("message",)
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    message: str
    def __init__(self, message: _Optional[str] = ...) -> None: ...

class StreamLogsRequest(_message.Message):
    __slots__ = ("job_name", "job_id", "follow", "controller", "tail")
    JOB_NAME_FIELD_NUMBER: _ClassVar[int]
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    FOLLOW_FIELD_NUMBER: _ClassVar[int]
    CONTROLLER_FIELD_NUMBER: _ClassVar[int]
    TAIL_FIELD_NUMBER: _ClassVar[int]
    job_name: str
    job_id: int
    follow: bool
    controller: bool
    tail: int
    def __init__(self, job_name: _Optional[str] = ..., job_id: _Optional[int] = ..., follow: bool = ..., controller: bool = ..., tail: _Optional[int] = ...) -> None: ...

class StreamLogsResponse(_message.Message):
    __slots__ = ("log_line", "exit_code")
    LOG_LINE_FIELD_NUMBER: _ClassVar[int]
    EXIT_CODE_FIELD_NUMBER: _ClassVar[int]
    log_line: str
    exit_code: int
    def __init__(self, log_line: _Optional[str] = ..., exit_code: _Optional[int] = ...) -> None: ...
