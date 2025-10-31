"""Request names."""
import enum


class RequestName(str, enum.Enum):
    """Enum of all the request names."""
    # General requests
    CHECK = 'check'
    ENABLED_CLOUDS = 'enabled_clouds'
    REALTIME_KUBERNETES_GPU_AVAILABILITY = (
        'realtime_kubernetes_gpu_availability')
    KUBERNETES_NODE_INFO = 'kubernetes_node_info'
    STATUS_KUBERNETES = 'status_kubernetes'
    LIST_ACCELERATORS = 'list_accelerators'
    LIST_ACCELERATOR_COUNTS = 'list_accelerator_counts'
    OPTIMIZE = 'optimize'
    # Cluster requests
    CLUSTER_LAUNCH = 'launch'
    CLUSTER_EXEC = 'exec'
    CLUSTER_STOP = 'stop'
    CLUSTER_STATUS = 'status'
    CLUSTER_ENDPOINTS = 'endpoints'
    CLUSTER_DOWN = 'down'
    CLUSTER_START = 'start'
    CLUSTER_AUTOSTOP = 'autostop'
    CLUSTER_QUEUE = 'queue'
    CLUSTER_JOB_STATUS = 'job_status'
    CLUSTER_JOB_CANCEL = 'cancel'
    CLUSTER_JOB_LOGS = 'logs'
    CLUSTER_JOB_DOWNLOAD_LOGS = 'download_logs'
    CLUSTER_COST_REPORT = 'cost_report'
    # Storage requests
    STORAGE_LS = 'storage_ls'
    STORAGE_DELETE = 'storage_delete'
    # Local requests
    LOCAL_UP = 'local_up'
    LOCAL_DOWN = 'local_down'
    # API requests
    API_CANCEL = 'api_cancel'
    ALL_CONTEXTS = 'all_contexts'
    # Managed jobs requests
    JOBS_LAUNCH = 'jobs.launch'
    JOBS_QUEUE = 'jobs.queue'
    JOBS_QUEUE_V2 = 'jobs.queue_v2'
    JOBS_CANCEL = 'jobs.cancel'
    JOBS_LOGS = 'jobs.logs'
    JOBS_DOWNLOAD_LOGS = 'jobs.download_logs'
    JOBS_POOL_APPLY = 'jobs.pool_apply'
    JOBS_POOL_DOWN = 'jobs.pool_down'
    JOBS_POOL_STATUS = 'jobs.pool_status'
    JOBS_POOL_LOGS = 'jobs.pool_logs'
    JOBS_POOL_SYNC_DOWN_LOGS = 'jobs.pool_sync_down_logs'
    # Serve requests
    SERVE_UP = 'serve.up'
    SERVE_UPDATE = 'serve.update'
    SERVE_DOWN = 'serve.down'
    SERVE_TERMINATE_REPLICA = 'serve.terminate_replica'
    SERVE_STATUS = 'serve.status'
    SERVE_LOGS = 'serve.logs'
    SERVE_SYNC_DOWN_LOGS = 'serve.sync_down_logs'
    # Volumes requests
    VOLUME_LIST = 'volume_list'
    VOLUME_DELETE = 'volume_delete'
    VOLUME_APPLY = 'volume_apply'
    # Workspaces requests
    WORKSPACES_GET = 'workspaces.get'
    WORKSPACES_UPDATE = 'workspaces.update'
    WORKSPACES_CREATE = 'workspaces.create'
    WORKSPACES_DELETE = 'workspaces.delete'
    WORKSPACES_GET_CONFIG = 'workspaces.get_config'
    WORKSPACES_UPDATE_CONFIG = 'workspaces.update_config'
    # SSH node pools requests
    SSH_NODE_POOLS_UP = 'ssh_node_pools.up'
    SSH_NODE_POOLS_DOWN = 'ssh_node_pools.down'
    # Internal request daemons
    REQUEST_DAEMON_STATUS_REFRESH = 'status-refresh'
    REQUEST_DAEMON_VOLUME_REFRESH = 'volume-refresh'
    REQUEST_DAEMON_MANAGED_JOB_STATUS_REFRESH = 'managed-job-status-refresh'
    REQUEST_DAEMON_SKY_SERVE_STATUS_REFRESH = 'sky-serve-status-refresh'
    REQUEST_DAEMON_POOL_STATUS_REFRESH = 'pool-status-refresh'
