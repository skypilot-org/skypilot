<!-- AUTO-GENERATED from sky/client/sdk.py -->
<!-- Run: python agent/scripts/generate_references.py -->

# SkyPilot Python SDK Reference

All SDK functions return a request ID (future). Use `sky.get(request_id)` to await the result.

```python
import sky

request_id = sky.launch(sky.Task.from_yaml("task.yaml"))
result = sky.get(request_id)
```

## Cluster Operations

### `sky.launch`

```python
sky.launch(task: Union['sky.Task', 'sky.Dag'], cluster_name: Optional[str] = None, retry_until_up: bool = False, idle_minutes_to_autostop: Optional[int] = None, wait_for: Optional[autostop_lib.AutostopWaitFor] = None, dryrun: bool = False, down: bool = False, backend: Optional['backends.Backend'] = None, optimize_target: common.OptimizeTarget = common.OptimizeTarget.COST, no_setup: bool = False, clone_disk_from: Optional[str] = None, fast: bool = False, _need_confirmation: bool = False, _is_launched_by_jobs_controller: bool = False, _is_launched_by_sky_serve_controller: bool = False, _disable_controller_check: bool = False) -> server_common.RequestId[Tuple[Optional[int], Optional['backends.ResourceHandle']]]
```

Launches a cluster or task.

The task's setup and run commands are executed under the task's workdir
(when specified, it is synced to remote cluster).  The task undergoes job
queue scheduling on the cluster.

Currently, the first argument must be a sky.Task, or (EXPERIMENTAL advanced
usage) a sky.Dag. In the latter case, currently it must contain a single
task; support for pipelines/general DAGs are in experimental branches.

**Example:**
    ```python

        import sky
        task = sky.Task(run='echo hello SkyPilot')
        task.set_resources(
            sky.Resources(infra='aws', accelerators='V100:4'))
        sky.launch(task, cluster_name='my-cluster')


**Args:**
    task: sky.Task, or sky.Dag (experimental; 1-task only) to launch.
    cluster_name: name of the cluster to create/reuse.  If None,
      auto-generate a name.
    retry_until_up: whether to retry launching the cluster until it is
      up.
    idle_minutes_to_autostop: automatically stop the cluster after this
        many minute of idleness, i.e., no running or pending jobs in the
        cluster's job queue. Idleness gets reset whenever setting-up/
        running/pending jobs are found in the job queue. Setting this
        flag is equivalent to running
        ``sky.launch(...)`` and then
        ``sky.autostop(idle_minutes=<minutes>)``. If set, the autostop
        config specified in the task' resources will be overridden by
        this parameter.
    wait_for: determines the condition for resetting the idleness timer.
        This option works in conjunction with ``idle_minutes_to_autostop``.
        Choices:

        1. "jobs_and_ssh" (default) - Wait for in-progress jobs and SSH
           connections to finish.
        2. "jobs" - Only wait for in-progress jobs.
        3. "none" - Wait for nothing; autostop right after
           ``idle_minutes_to_autostop``.
    dryrun: if True, do not actually launch the cluster.
    down: Tear down the cluster after all jobs finish (successfully or
        abnormally). If --idle-minutes-to-autostop is also set, the
        cluster will be torn down after the specified idle time.
        Note that if errors occur during provisioning/data syncing/setting
        up, the cluster will not be torn down for debugging purposes. If
        set, the autostop config specified in the task' resources will be
        overridden by this parameter.
    backend: backend to use.  If None, use the default backend
      (CloudVMRayBackend).
    optimize_target: target to optimize for. Choices: OptimizeTarget.COST,
      OptimizeTarget.TIME.
    no_setup: if True, do not re-run setup commands.
    clone_disk_from: [Experimental] if set, clone the disk from the
      specified cluster. This is useful to migrate the cluster to a
      different availability zone or region.
    fast: [Experimental] If the cluster is already up and available,
      skip provisioning and setup steps.
    _need_confirmation: (Internal only) If True, show the confirmation
        prompt.

**Returns:**
    The request ID of the launch request.

**Request Returns:**
    job_id (Optional[int]): the job ID of the submitted job. None if the
      backend is not ``CloudVmRayBackend``, or no job is submitted to the
      cluster.
    handle (Optional[backends.ResourceHandle]): the handle to the cluster.
      None if dryrun.

**Request Raises:**
    exceptions.ClusterOwnerIdentityMismatchError: if the cluster is owned
      by another user.
    exceptions.InvalidClusterNameError: if the cluster name is invalid.
    exceptions.ResourcesMismatchError: if the requested resources
      do not match the existing cluster.
    exceptions.NotSupportedError: if required features are not supported
      by the backend/cloud/cluster.
    exceptions.ResourcesUnavailableError: if the requested resources
      cannot be satisfied. The failover_history of the exception will be set
      as:

      1. Empty: iff the first-ever sky.optimize() fails to find a feasible
         resource; no pre-check or actual launch is attempted.

      2. Non-empty: iff at least 1 exception from either our pre-checks
         (e.g., cluster name invalid) or a region/zone throwing resource
         unavailability.

    exceptions.CommandError: any ssh command error.
    exceptions.NoCloudAccessError: if all clouds are disabled.

Other exceptions may be raised depending on the backend.

### `sky.exec`

```python
sky.exec(task: Union['sky.Task', 'sky.Dag'], cluster_name: Optional[str] = None, dryrun: bool = False, down: bool = False, backend: Optional['backends.Backend'] = None) -> server_common.RequestId[Tuple[Optional[int], Optional['backends.ResourceHandle']]]
```

Executes a task on an existing cluster.

**This function performs two actions:**

(1) workdir syncing, if the task has a workdir specified;
(2) executing the task's ``run`` commands.

All other steps (provisioning, setup commands, file mounts syncing) are
skipped.  If any of those specifications changed in the task, this function
will not reflect those changes.  To ensure a cluster's setup is up to date,
use ``sky.launch()`` instead.

**Execution and scheduling behavior:**

- The task will undergo job queue scheduling, respecting any specified
  resource requirement. It can be executed on any node of the cluster with
  enough resources.
- The task is run under the workdir (if specified).
- The task is run non-interactively (without a pseudo-terminal or
  pty), so interactive commands such as ``htop`` do not work.
  Use ``ssh my_cluster`` instead.

**Args:**
    task: sky.Task, or sky.Dag (experimental; 1-task only) containing the
      task to execute.
    cluster_name: name of an existing cluster to execute the task.
    dryrun: if True, do not actually execute the task.
    down: Tear down the cluster after all jobs finish (successfully or
      abnormally). If --idle-minutes-to-autostop is also set, the
      cluster will be torn down after the specified idle time.
      Note that if errors occur during provisioning/data syncing/setting
      up, the cluster will not be torn down for debugging purposes.
    backend: backend to use.  If None, use the default backend
      (CloudVMRayBackend).

**Returns:**
    The request ID of the exec request.


**Request Returns:**
    job_id (Optional[int]): the job ID of the submitted job. None if the
      backend is not CloudVmRayBackend, or no job is submitted to
      the cluster.
    handle (Optional[backends.ResourceHandle]): the handle to the cluster.
      None if dryrun.

**Request Raises:**
    ValueError: if the specified cluster is not in UP status.
    sky.exceptions.ClusterDoesNotExist: if the specified cluster does not
      exist.
    sky.exceptions.NotSupportedError: if the specified cluster is a
      controller that does not support this operation.

### `sky.stop`

```python
sky.stop(cluster_name: str, purge: bool = False, graceful: bool = False, graceful_timeout: Optional[int] = None) -> server_common.RequestId[None]
```

Stops a cluster.

Data on attached disks is not lost when a cluster is stopped.  Billing for
the instances will stop, while the disks will still be charged.  Those
disks will be reattached when restarting the cluster.

Currently, spot instance clusters cannot be stopped (except for GCP, which
does allow disk contents to be preserved when stopping spot VMs).

**Args:**
    cluster_name: name of the cluster to stop.
    purge: (Advanced) Forcefully mark the cluster as stopped in SkyPilot's
        cluster table, even if the actual cluster stop operation failed on
        the cloud. WARNING: This flag should only be set sparingly in
        certain manual troubleshooting scenarios; with it set, it is the
        user's responsibility to ensure there are no leaked instances and
        related resources.

**Returns:**
    The request ID of the stop request.

**Request Returns:**
    None

**Request Raises:**
    sky.exceptions.ClusterDoesNotExist: the specified cluster does not
        exist.
    RuntimeError: failed to stop the cluster.
    sky.exceptions.NotSupportedError: if the specified cluster is a spot
        cluster, or a TPU VM Pod cluster, or the managed jobs controller.

### `sky.start`

```python
sky.start(cluster_name: str, idle_minutes_to_autostop: Optional[int] = None, wait_for: Optional[autostop_lib.AutostopWaitFor] = None, retry_until_up: bool = False, down: bool = False, force: bool = False) -> server_common.RequestId['backends.CloudVmRayResourceHandle']
```

Restart a cluster.

If a cluster is previously stopped (status is STOPPED) or failed in
provisioning/runtime installation (status is INIT), this function will
attempt to start the cluster.  In the latter case, provisioning and runtime
installation will be retried.

Auto-failover provisioning is not used when restarting a stopped
cluster. It will be started on the same cloud, region, and zone that were
chosen before.

If a cluster is already in the UP status, this function has no effect.

**Args:**
    cluster_name: name of the cluster to start.
    idle_minutes_to_autostop: automatically stop the cluster after this
        many minute of idleness, i.e., no running or pending jobs in the
        cluster's job queue. Idleness gets reset whenever setting-up/
        running/pending jobs are found in the job queue. Setting this
        flag is equivalent to running ``sky.launch()`` and then
        ``sky.autostop(idle_minutes=<minutes>)``. If not set, the
        cluster will not be autostopped.
    wait_for: determines the condition for resetting the idleness timer.
        This option works in conjunction with ``idle_minutes_to_autostop``.
        Choices:

        1. "jobs_and_ssh" (default) - Wait for in-progress jobs and SSH
           connections to finish.
        2. "jobs" - Only wait for in-progress jobs.
        3. "none" - Wait for nothing; autostop right after
           ``idle_minutes_to_autostop``.
    retry_until_up: whether to retry launching the cluster until it is
        up.
    down: Autodown the cluster: tear down the cluster after specified
        minutes of idle time after all jobs finish (successfully or
        abnormally). Requires ``idle_minutes_to_autostop`` to be set.
    force: whether to force start the cluster even if it is already up.
        Useful for upgrading SkyPilot runtime.

**Returns:**
    The request ID of the start request.

**Request Returns:**
    None

**Request Raises:**
    ValueError: argument values are invalid: (1) if ``down`` is set to True
        but ``idle_minutes_to_autostop`` is None; (2) if the specified
        cluster is the managed jobs controller, and either
        ``idle_minutes_to_autostop`` is not None or ``down`` is True (omit
        them to use the default autostop settings).
    sky.exceptions.ClusterDoesNotExist: the specified cluster does not
        exist.
    sky.exceptions.NotSupportedError: if the cluster to restart was
        launched using a non-default backend that does not support this
        operation.
    sky.exceptions.ClusterOwnerIdentitiesMismatchError: if the cluster to
        restart was launched by a different user.

### `sky.down`

```python
sky.down(cluster_name: str, purge: bool = False, graceful: bool = False, graceful_timeout: Optional[int] = None) -> server_common.RequestId[None]
```

Tears down a cluster.

Tearing down a cluster will delete all associated resources (all billing
stops), and any data on the attached disks will be lost.  Accelerators
(e.g., TPUs) that are part of the cluster will be deleted too.

**Args:**
    cluster_name: name of the cluster to down.
    purge: (Advanced) Forcefully remove the cluster from SkyPilot's cluster
        table, even if the actual cluster termination failed on the cloud.
        WARNING: This flag should only be set sparingly in certain manual
        troubleshooting scenarios; with it set, it is the user's
        responsibility to ensure there are no leaked instances and related
        resources.
    graceful: Cancel the user's task but block until MOUNT_CACHED data is
        fully uploaded. This helps with preserving user data integrity.
    graceful_timeout: If not None, sets a timeout for the graceful option
        above (in seconds).

**Returns:**
    The request ID of the down request.

**Request Returns:**
    None

**Request Raises:**
    sky.exceptions.ClusterDoesNotExist: the specified cluster does not
        exist.
    RuntimeError: failed to tear down the cluster.
    sky.exceptions.NotSupportedError: the specified cluster is the managed
        jobs controller.

### `sky.status`

```python
sky.status(cluster_names: Optional[List[str]] = None, refresh: common.StatusRefreshMode = common.StatusRefreshMode.NONE, all_users: bool = False, *, _include_credentials: bool = False, _summary_response: bool = False) -> server_common.RequestId[List[responses.StatusResponse]]
```

Gets cluster statuses.

If cluster_names is given, return those clusters. Otherwise, return all
clusters.

**Each cluster can have one of the following statuses:**

- ``INIT``: The cluster may be live or down. It can happen in the following
  cases:

  - Ongoing provisioning or runtime setup. (A ``sky.launch()`` has started
    but has not completed.)
  - Or, the cluster is in an abnormal state, e.g., some cluster nodes are
    down, or the SkyPilot runtime is unhealthy. (To recover the cluster,
    try ``sky launch`` again on it.)

- ``UP``: Provisioning and runtime setup have succeeded and the cluster is
  live.  (The most recent ``sky.launch()`` has completed successfully.)

- ``STOPPED``: The cluster is stopped and the storage is persisted. Use
  ``sky.start()`` to restart the cluster.

**Autostop column:**

- The autostop column indicates how long the cluster will be autostopped
  after minutes of idling (no jobs running). If ``to_down`` is True, the
  cluster will be autodowned, rather than autostopped.

Getting up-to-date cluster statuses:

- In normal cases where clusters are entirely managed by SkyPilot (i.e., no
  manual operations in cloud consoles) and no autostopping is used, the
  table returned by this command will accurately reflect the cluster
  statuses.

- In cases where the clusters are changed outside of SkyPilot (e.g., manual
  operations in cloud consoles; unmanaged spot clusters getting preempted)
  or for autostop-enabled clusters, use ``refresh=True`` to query the
  latest cluster statuses from the cloud providers.

**Args:**
    cluster_names: a list of cluster names to query. If not
        provided, all clusters will be queried.
    refresh: whether to query the latest cluster statuses from the cloud
        provider(s).
    all_users: whether to include all users' clusters. By default, only
        the current user's clusters are included.
    _include_credentials: (internal) whether to include cluster ssh
        credentials in the response (default: False).

**Returns:**
    The request ID of the status request.

**Request Returns:**
    cluster_records (List[Dict[str, Any]]): A list of dicts, with each dict
      containing the information of a cluster. If a cluster is found to be
      terminated or not found, it will be omitted from the returned list.

      ```python

        {
          'name': (str) cluster name,
          'launched_at': (int) timestamp of last launch on this cluster,
          'handle': (ResourceHandle) an internal handle to the cluster,
          'last_use': (str) the last command/entrypoint that affected this
          cluster,
          'status': (sky.ClusterStatus) cluster status,
          'autostop': (int) idle time before autostop,
          'to_down': (bool) whether autodown is used instead of autostop,
          'metadata': (dict) metadata of the cluster,
          'user_hash': (str) user hash of the cluster owner,
          'user_name': (str) user name of the cluster owner,
          'resources_str': (str) the resource string representation of the
            cluster,
        }

### `sky.autostop`

```python
sky.autostop(cluster_name: str, idle_minutes: int, wait_for: Optional[autostop_lib.AutostopWaitFor] = None, down: bool = False, hook: Optional[str] = None, hook_timeout: Optional[int] = None) -> server_common.RequestId[None]
```

Schedules an autostop/autodown for a cluster.

Autostop/autodown will automatically stop or teardown a cluster when it
becomes idle for a specified duration.  Idleness means there are no
in-progress (pending/running) jobs in a cluster's job queue.

Idleness time of a cluster is reset to zero, whenever:

- A job is submitted (``sky.launch()`` or ``sky.exec()``).

- The cluster has restarted.

- An autostop is set when there is no active setting. (Namely, either
  there's never any autostop setting set, or the previous autostop setting
  was canceled.) This is useful for restarting the autostop timer.

Example: say a cluster without any autostop set has been idle for 1 hour,
then an autostop of 30 minutes is set. The cluster will not be immediately
autostopped. Instead, the idleness timer only starts counting after the
autostop setting was set.

When multiple autostop settings are specified for the same cluster, the
last setting takes precedence.

**Args:**
    cluster_name: name of the cluster.
    idle_minutes: the number of minutes of idleness (no pending/running
        jobs) after which the cluster will be stopped automatically. Setting
        to a negative number cancels any autostop/autodown setting.
    wait_for: determines the condition for resetting the idleness timer.
        This option works in conjunction with ``idle_minutes``.
        Choices:

        1. "jobs_and_ssh" (default) - Wait for in-progress jobs and SSH
           connections to finish.
        2. "jobs" - Only wait for in-progress jobs.
        3. "none" - Wait for nothing; autostop right after ``idle_minutes``.
    down: if true, use autodown (tear down the cluster; non-restartable),
        rather than autostop (restartable).
    hook: optional script to execute on the remote cluster before autostop.
        The script runs before the cluster is stopped or torn down. If the
        hook fails, autostop will still proceed but a warning will be
        logged.
    hook_timeout: timeout in seconds for hook execution. If None, uses
        DEFAULT_AUTOSTOP_HOOK_TIMEOUT_SECONDS (3600 = 1 hour). The hook will
        be terminated if it exceeds this timeout.

**Returns:**
    The request ID of the autostop request.

**Request Returns:**
    None

**Request Raises:**
    ValueError: if arguments are invalid.
    sky.exceptions.ClusterDoesNotExist: if the cluster does not exist.
    sky.exceptions.ClusterNotUpError: if the cluster is not UP.
    sky.exceptions.NotSupportedError: if the cluster is not based on
        CloudVmRayBackend or the cluster is TPU VM Pod.
    sky.exceptions.ClusterOwnerIdentityMismatchError: if the current user is
        not the same as the user who created the cluster.
    sky.exceptions.CloudUserIdentityError: if we fail to get the current
        user identity.

### `sky.cost_report`

```python
sky.cost_report(days: Optional[int] = None) -> server_common.RequestId[List[Dict[str, Any]]]
```

Gets all cluster cost reports, including those that have been downed.

The estimated cost column indicates price for the cluster based on the type
of resources being used and the duration of use up until the call to
status. This means if the cluster is UP, successive calls to report will
show increasing price. The estimated cost is calculated based on the local
cache of the cluster status, and may not be accurate for the cluster with
autostop/use_spot set or terminated/stopped on the cloud console.

**Args:**
    days: The number of days to get the cost report for. If not provided,
        the default is 30 days.

**Returns:**
    The request ID of the cost report request.

**Request Returns:**
    cluster_cost_records (List[Dict[str, Any]]): A list of dicts, with each
      dict containing the cost information of a cluster.

      ```python

        {
          'name': (str) cluster name,
          'launched_at': (int) timestamp of last launch on this cluster,
          'duration': (int) total seconds that cluster was up and running,
          'last_use': (str) the last command/entrypoint that affected this
          'num_nodes': (int) number of nodes launched for cluster,
          'resources': (resources.Resources) type of resource launched,
          'cluster_hash': (str) unique hash identifying cluster,
          'usage_intervals': (List[Tuple[int, int]]) cluster usage times,
          'total_cost': (float) cost given resources and usage intervals,
        }

### `sky.endpoints`

```python
sky.endpoints(cluster: str, port: Optional[Union[int, str]] = None) -> server_common.RequestId[Dict[int, str]]
```

Gets the endpoint for a given cluster and port number (endpoint).

**Example:**
    ```python

        import sky
        request_id = sky.endpoints('test-cluster')
        sky.get(request_id)


**Args:**
    cluster: The name of the cluster.
    port: The port number to get the endpoint for. If None, endpoints
        for all ports are returned.

**Returns:**
    The request ID of the endpoints request.

**Request Returns:**
    A dictionary of port numbers to endpoints.
    If port is None, the dictionary contains all
        ports:endpoints exposed on the cluster.

**Request Raises:**
    ValueError: if the cluster is not UP or the endpoint is not exposed.
    RuntimeError: if the cluster has no ports to be exposed or no endpoints
        are exposed yet.

## Job Operations

### `sky.queue`

```python
sky.queue(cluster_name: str, skip_finished: bool = False, all_users: bool = False) -> server_common.RequestId[List[responses.ClusterJobRecord]]
```

Gets the job queue of a cluster.

**Args:**
    cluster_name: name of the cluster.
    skip_finished: if True, skip finished jobs.
    all_users: if True, return jobs from all users.


**Returns:**
    The request ID of the queue request.

**Request Returns:**
    job_records (List[responses.ClusterJobRecord]): A list of job records
        for each job in the queue.

        ```python

            [
                {
                    'job_id': (int) job id,
                    'job_name': (str) job name,
                    'username': (str) username,
                    'user_hash': (str) user hash,
                    'submitted_at': (int) timestamp of submitted,
                    'start_at': (int) timestamp of started,
                    'end_at': (int) timestamp of ended,
                    'resources': (str) resources,
                    'status': (job_lib.JobStatus) job status,
                    'log_path': (str) log path,
                }
            ]

**Request Raises:**
    sky.exceptions.ClusterDoesNotExist: if the cluster does not exist.
    sky.exceptions.ClusterNotUpError: if the cluster is not UP.
    sky.exceptions.NotSupportedError: if the cluster is not based on
        ``CloudVmRayBackend``.
    sky.exceptions.ClusterOwnerIdentityMismatchError: if the current user is
        not the same as the user who created the cluster.
    sky.exceptions.CloudUserIdentityError: if we fail to get the current
        user identity.
    sky.exceptions.CommandError: if failed to get the job queue with ssh.

### `sky.job_status`

```python
sky.job_status(cluster_name: str, job_ids: Optional[List[int]] = None) -> server_common.RequestId[Dict[Optional[int], Optional['job_lib.JobStatus']]]
```

Gets the status of jobs on a cluster.

**Args:**
    cluster_name: name of the cluster.
    job_ids: job ids. If None, get the status of the last job.

**Returns:**
    The request ID of the job status request.

**Request Returns:**
    job_statuses (Dict[Optional[int], Optional[job_lib.JobStatus]]): A
        mapping of job_id to job statuses. The status will be None if the
        job does not exist. If job_ids is None and there is no job on the
        cluster, it will return {None: None}.

**Request Raises:**
    sky.exceptions.ClusterDoesNotExist: if the cluster does not exist.
    sky.exceptions.ClusterNotUpError: if the cluster is not UP.
    sky.exceptions.NotSupportedError: if the cluster is not based on
        ``CloudVmRayBackend``.
    sky.exceptions.ClusterOwnerIdentityMismatchError: if the current user is
        not the same as the user who created the cluster.
    sky.exceptions.CloudUserIdentityError: if we fail to get the current
        user identity.

### `sky.cancel`

```python
sky.cancel(cluster_name: str, all: bool = False, all_users: bool = False, job_ids: Optional[List[int]] = None, _try_cancel_if_cluster_is_init: bool = False) -> server_common.RequestId[None]
```

Cancels jobs on a cluster.

**Args:**
    cluster_name: name of the cluster.
    all: if True, cancel all jobs.
    all_users: if True, cancel all jobs from all users.
    job_ids: a list of job IDs to cancel.
    _try_cancel_if_cluster_is_init: (bool) whether to try cancelling the job
        even if the cluster is not UP, but the head node is still alive.
        This is used by the jobs controller to cancel the job when the
        worker node is preempted in the spot cluster.

**Returns:**
    The request ID of the cancel request.

**Request Returns:**
    None

**Request Raises:**
    ValueError: if arguments are invalid.
    sky.exceptions.ClusterDoesNotExist: if the cluster does not exist.
    sky.exceptions.ClusterNotUpError: if the cluster is not UP.
    sky.exceptions.NotSupportedError: if the specified cluster is a
        controller that does not support this operation.
    sky.exceptions.ClusterOwnerIdentityMismatchError: if the current user is
        not the same as the user who created the cluster.
    sky.exceptions.CloudUserIdentityError: if we fail to get the current
        user identity.

### `sky.tail_logs`

```python
sky.tail_logs(cluster_name: str, job_id: Optional[int], follow: bool, tail: int = 0, output_stream: Optional['io.TextIOBase'] = None, *, preload_content: bool = True) -> Union[int, Iterator[Optional[str]]]
```

Tails the logs of a job.

**Args:**
    cluster_name: name of the cluster.
    job_id: job id.
    follow: if True, follow the logs. Otherwise, return the logs
        immediately.
    tail: if > 0, tail the last N lines of the logs.
    output_stream: the stream to write the logs to. If None, print to the
        console. Cannot be used with preload_content=False.
    preload_content: if False, returns an Iterator[str | None] containing
        the logs without the function blocking on the retrieval of entire
        log. Iterator returns None when the log has been completely
        streamed. Default True. Cannot be used with output_stream.

**Returns:**
    If preload_content is True:
        Exit code based on success or failure of the job. 0 if success,
        100 if the job failed. See exceptions.JobExitCode for possible exit
        codes.
    If preload_content is False:
        Iterator[str | None] containing the logs without the function
        blocking on the retrieval of entire log. Iterator returns None
        when the log has been completely streamed.

**Request Raises:**
    ValueError: if arguments are invalid or the cluster is not supported.
    sky.exceptions.ClusterDoesNotExist: if the cluster does not exist.
    sky.exceptions.ClusterNotUpError: if the cluster is not UP.
    sky.exceptions.NotSupportedError: if the cluster is not based on
      CloudVmRayBackend.
    sky.exceptions.ClusterOwnerIdentityMismatchError: if the current user is
      not the same as the user who created the cluster.
    sky.exceptions.CloudUserIdentityError: if we fail to get the current
      user identity.

### `sky.tail_provision_logs`

```python
sky.tail_provision_logs(cluster_name: str, worker: Optional[int] = None, follow: bool = True, tail: int = 0, output_stream: Optional['io.TextIOBase'] = None) -> int
```

Tails the provisioning logs (provision.log) for a cluster.

**Args:**
    cluster_name: name of the cluster.
    worker: worker id in multi-node cluster.
         If None, stream the logs of the head node.
    follow: follow the logs.
    tail: lines from end to tail.
    output_stream: optional stream to write logs.
**Returns:**
    Exit code 0 on streaming success; raises on HTTP error.

### `sky.tail_autostop_logs`

```python
sky.tail_autostop_logs(cluster_name: str, follow: bool = True, tail: int = 0) -> int
```

Tails the autostop hook logs (autostop_hook.log) for a cluster.

**Args:**
    cluster_name: name of the cluster.
    follow: whether to follow the logs.
    tail: number of lines to display from the end of the log file.

**Returns:**
    Exit code 0 on streaming success; non-zero on failure.

**Request Raises:**
    ValueError: if arguments are invalid or the cluster is not supported.
    sky.exceptions.ClusterDoesNotExist: if the cluster does not exist.
    sky.exceptions.ClusterNotUpError: if the cluster is not UP.
    sky.exceptions.NotSupportedError: if the cluster is not based on
      CloudVmRayBackend.
    sky.exceptions.ClusterOwnerIdentityMismatchError: if the current user is
      not the same as the user who created the cluster.
    sky.exceptions.CloudUserIdentityError: if we fail to get the current
      user identity.

### `sky.download_logs`

```python
sky.download_logs(cluster_name: str, job_ids: Optional[List[str]]) -> Dict[str, str]
```

Downloads the logs of jobs.

**Args:**
    cluster_name: (str) name of the cluster.
    job_ids: (List[str]) job ids.

**Returns:**
    The request ID of the download_logs request.

**Request Returns:**
    job_log_paths (Dict[str, str]): a mapping of job_id to local log path.

**Request Raises:**
    sky.exceptions.ClusterDoesNotExist: if the cluster does not exist.
    sky.exceptions.ClusterNotUpError: if the cluster is not UP.
    sky.exceptions.NotSupportedError: if the cluster is not based on
      CloudVmRayBackend.
    sky.exceptions.ClusterOwnerIdentityMismatchError: if the current user is
      not the same as the user who created the cluster.
    sky.exceptions.CloudUserIdentityError: if we fail to get the current
      user identity.

## Infrastructure & Configuration

### `sky.check`

```python
sky.check(infra_list: Optional[Tuple[str, ...]], verbose: bool, workspace: Optional[str] = None) -> server_common.RequestId[Dict[str, Dict[str, List[str]]]]
```

Checks the credentials to enable clouds.

**Args:**
    infra: The infra to check.
    verbose: Whether to show verbose output.
    workspace: The workspace to check. If None, all workspaces will be
    checked.

**Returns:**
    The request ID of the check request.

**Request Returns:**
    Dict mapping workspace name to a dict of cloud name to list of
    enabled capability strings (e.g. 'compute', 'storage').

### `sky.enabled_clouds`

```python
sky.enabled_clouds(workspace: Optional[str] = None, expand: bool = False) -> server_common.RequestId[List[str]]
```

Gets the enabled clouds.

**Args:**
    workspace: The workspace to get the enabled clouds for. If None, the
    active workspace will be used.
    expand: Whether to expand Kubernetes and SSH to list of resource pools.

**Returns:**
    The request ID of the enabled clouds request.

**Request Returns:**
    A list of enabled clouds in string format.

### `sky.list_accelerators`

```python
sky.list_accelerators(gpus_only: bool = True, name_filter: Optional[str] = None, region_filter: Optional[str] = None, quantity_filter: Optional[int] = None, clouds: Optional[Union[List[str], str]] = None, all_regions: bool = False, require_price: bool = True, case_sensitive: bool = True) -> server_common.RequestId[Dict[str, List['catalog.common.InstanceTypeInfo']]]
```

Lists the names of all accelerators offered by Sky.

This will include all accelerators offered by Sky, including those
that may not be available in the user's account.

**Args:**
    gpus_only: Whether to only list GPU accelerators.
    name_filter: The name filter.
    region_filter: The region filter.
    quantity_filter: The quantity filter.
    clouds: The clouds to list.
    all_regions: Whether to list all regions.
    require_price: Whether to require price.
    case_sensitive: Whether to case sensitive.

**Returns:**
    The request ID of the list accelerator counts request.

**Request Returns:**
    acc_to_instance_type_dict (Dict[str, List[InstanceTypeInfo]]): A
        dictionary of canonical accelerator names mapped to a list of
        instance type offerings. See usage in cli.py.

### `sky.kubernetes_node_info`

```python
sky.kubernetes_node_info(context: Optional[str] = None) -> server_common.RequestId['models.KubernetesNodesInfo']
```

Gets the resource information for all the nodes in the cluster.

Currently only GPU resources are supported. The function returns the total
number of GPUs available on the node and the number of free GPUs on the
node.

If the user does not have sufficient permissions to list pods in all
namespaces, the function will return free GPUs as -1.

**Args:**
    context: The Kubernetes context. If None, the default context is used.

**Returns:**
    The request ID of the Kubernetes node info request.

**Request Returns:**
    KubernetesNodesInfo: A model that contains the node info map and other
        information.

### `sky.realtime_kubernetes_gpu_availability`

```python
sky.realtime_kubernetes_gpu_availability(context: Optional[str] = None, name_filter: Optional[str] = None, quantity_filter: Optional[int] = None, is_ssh: Optional[bool] = None) -> server_common.RequestId[List[Tuple[str, List['models.RealtimeGpuAvailability']]]]
```

Gets the real-time Kubernetes GPU availability.

**Returns:**
    The request ID of the real-time Kubernetes GPU availability request.

### `sky.optimize`

```python
sky.optimize(dag: 'sky.Dag', minimize: common.OptimizeTarget = common.OptimizeTarget.COST, admin_policy_request_options: Optional[admin_policy.RequestOptions] = None) -> server_common.RequestId['sky.Dag']
```

Finds the best execution plan for the given DAG.

**Args:**
    dag: the DAG to optimize.
    minimize: whether to minimize cost or time.
    admin_policy_request_options: Request options used for admin policy
        validation. This is only required when a admin policy is in use,
        see: https://docs.skypilot.co/en/latest/cloud-setup/policy.html

**Returns:**
    The request ID of the optimize request.

**Request Returns:**
    optimized_dag (str): The optimized DAG in YAML format.

**Request Raises:**
    exceptions.ResourcesUnavailableError: if no resources are available
        for a task.
    exceptions.NoCloudAccessError: if no public clouds are enabled.

### `sky.validate`

```python
sky.validate(dag: 'sky.Dag', workdir_only: bool = False, admin_policy_request_options: Optional[admin_policy.RequestOptions] = None) -> None
```

Validates the tasks.

The file paths (workdir and file_mounts) are validated on the client side
while the rest (e.g. resource) are validated on server side.

Raises exceptions if the DAG is invalid.

**Args:**
    dag: the DAG to validate.
    workdir_only: whether to only validate the workdir. This is used for
        `exec` as it does not need other files/folders in file_mounts.
    admin_policy_request_options: Request options used for admin policy
        validation. This is only required when a admin policy is in use,
        see: https://docs.skypilot.co/en/latest/cloud-setup/policy.html

### `sky.reload_config`

```python
sky.reload_config() -> None
```

Reloads the client-side config.

## Storage & Volumes

### `sky.storage_ls`

```python
sky.storage_ls() -> server_common.RequestId[List[responses.StorageRecord]]
```

Gets the storages.

**Returns:**
    The request ID of the storage list request.

**Request Returns:**
    storage_records (List[responses.StorageRecord]):
        A list of storage records.

### `sky.storage_delete`

```python
sky.storage_delete(name: str) -> server_common.RequestId[None]
```

Deletes a storage.

**Args:**
    name: The name of the storage to delete.

**Returns:**
    The request ID of the storage delete request.

**Request Returns:**
    None

**Request Raises:**
    ValueError: If the storage does not exist.

## API Server

### `sky.api_start`

```python
sky.api_start(*, deploy: bool = False, host: str = '127.0.0.1', foreground: bool = False, metrics: bool = False, metrics_port: Optional[int] = None, enable_basic_auth: bool = False) -> None
```

Starts the API server.

It checks the existence of the API server and starts it if it does not
exist.

**Args:**
    deploy: Whether to deploy the API server, i.e. fully utilize the
        resources of the machine.
    host: The host to deploy the API server. It will be set to 0.0.0.0
        if deploy is True, to allow remote access.
    foreground: Whether to run the API server in the foreground (run in
        the current process).
    metrics: Whether to export metrics of the API server.
    metrics_port: The port to export metrics of the API server.
    enable_basic_auth: Whether to enable basic authentication
        in the API server.
**Returns:**
    None

### `sky.api_stop`

```python
sky.api_stop() -> None
```

Stops the API server.

It will do nothing if the API server is remotely hosted.

**Returns:**
    None

### `sky.api_status`

```python
sky.api_status(request_ids: Optional[List[Union[server_common.RequestId[T], str]]] = None, all_status: bool = False, limit: Optional[int] = None, fields: Optional[List[str]] = None, cluster_name: Optional[str] = None) -> List[payloads.RequestPayload]
```

Lists all requests.

**Args:**
    request_ids: The prefixes of the request IDs of the requests to query.
        If None, all requests are queried.
    all_status: Whether to list all finished requests as well. This argument
        is ignored if request_ids is not None.
    limit: The number of requests to show. If None, show all requests.
    fields: The fields to get. If None, get all fields.
    cluster_name: Filter requests by cluster name.
        If None, show all requests.

**Returns:**
    A list of request payloads.

### `sky.api_info`

```python
sky.api_info() -> responses.APIHealthResponse
```

Gets the server's status, commit and version.

**Returns:**
    A dictionary containing the server's status, commit and version.

    ```python

        {
            'status': 'healthy',
            'api_version': '1',
            'commit': 'abc1234567890',
            'version': '1.0.0',
            'version_on_disk': '1.0.0',
            'user': {
                'name': 'test@example.com',
                'id': '12345abcd',
            },
        }

    Note that user may be None if we are not using an auth proxy.

### `sky.api_cancel`

```python
sky.api_cancel(request_ids: Optional[Union[server_common.RequestId[T], List[server_common.RequestId[T]], str, List[str]]] = None, all_users: bool = False, silent: bool = False) -> server_common.RequestId[List[str]]
```

Aborts a request or all requests.

**Args:**
    request_ids: The request ID(s) to abort. Can be a single string or a
        list of strings.
    all_users: Whether to abort all requests from all users.
    silent: Whether to suppress the output.

**Returns:**
    The request ID of the abort request itself.

**Request Returns:**
    A list of request IDs that were cancelled.

**Raises:**
    click.BadParameter: If no request ID is specified and not all or
        all_users is not set.

### `sky.api_server_logs`

```python
sky.api_server_logs(follow: bool = True, tail: Optional[int] = None) -> None
```

Streams the API server logs.

**Args:**
    follow: Whether to follow the logs.
    tail: the number of lines to show from the end of the logs.
        If None, show all logs.

**Returns:**
    None

### `sky.api_login`

```python
sky.api_login(endpoint: Optional[str] = None, relogin: bool = False, service_account_token: Optional[str] = None) -> None
```

Logs into a SkyPilot API server.

This sets the endpoint globally, i.e., all SkyPilot CLI and SDK calls will
use this endpoint.

To temporarily override the endpoint, use the environment variable
`SKYPILOT_API_SERVER_ENDPOINT` instead.

**Args:**
    endpoint: The endpoint of the SkyPilot API server, e.g.,
        http://1.2.3.4:46580 or https://skypilot.mydomain.com.
    relogin: Whether to force relogin with OAuth2 when enabled.
    service_account_token: Service account token for authentication.

**Returns:**
    None

### `sky.api_logout`

```python
sky.api_logout() -> None
```

Logout of the API server.

Clears all cookies and settings stored in ~/.sky/config.yaml

## Utilities

### `sky.dashboard`

```python
sky.dashboard(starting_page: Optional[str] = None) -> None
```

Starts the dashboard for SkyPilot.

### `sky.get`

```python
sky.get(request_id: server_common.RequestId[T]) -> T
```

Waits for and gets the result of a request.

This function will not check the server health since /api/get is typically
not the first API call in an SDK session and checking the server health
may cause GET /api/get being sent to a restarted API server.

**Args:**
    request_id: The request ID of the request to get. May be a full request
        ID or a prefix.

**Returns:**
    The ``Request Returns`` of the specified request. See the documentation
    of the specific requests above for more details.

**Raises:**
    Exception: It raises the same exceptions as the specific requests,
        see ``Request Raises`` in the documentation of the specific requests
        above.

### `sky.stream_and_get`

```python
sky.stream_and_get(request_id: Optional[server_common.RequestId[T]] = None, log_path: Optional[str] = None, tail: Optional[int] = None, follow: bool = True, output_stream: Optional['io.TextIOBase'] = None) -> Optional[T]
```

Streams the logs of a request or a log file and gets the final result.

This will block until the request is finished. The request id can be a
prefix of the full request id.

**Args:**
    request_id: The request ID of the request to stream. May be a full
        request ID or a prefix.
        If None, the latest request submitted to the API server is streamed.
        Using None request_id is not recommended in multi-user environments.
    log_path: The path to the log file to stream.
    tail: The number of lines to show from the end of the logs.
        If None, show all logs.
    follow: Whether to follow the logs.
    output_stream: The output stream to write to. If None, print to the
        console.

**Returns:**
    The ``Request Returns`` of the specified request. See the documentation
    of the specific requests above for more details.
    If follow is False, will always return None. See note on
    stream_response.

**Raises:**
    Exception: It raises the same exceptions as the specific requests,
        see ``Request Raises`` in the documentation of the specific requests
        above.

### `sky.workspaces`

```python
sky.workspaces() -> server_common.RequestId[Dict[str, Any]]
```

Gets the workspaces.

## Other Functions

### `sky.kubernetes_label_gpus`

```python
sky.kubernetes_label_gpus(context: Optional[str] = None, cleanup_only: bool = False, wait_for_completion: bool = True) -> server_common.RequestId[Dict[str, Any]]
```

Labels GPU nodes in a Kubernetes cluster for use with SkyPilot.

Note: Currently only supports NVIDIA GPUs. AMD GPUs must be labeled
manually.

**Args:**
    context: Kubernetes context to use. If None, uses current context.
    cleanup_only: If True, only cleanup existing labeling resources.
    wait_for_completion: If True, wait for labeling jobs to complete.

**Returns:**
    RequestId for the labeling operation.

### `sky.list_accelerator_counts`

```python
sky.list_accelerator_counts(gpus_only: bool = True, name_filter: Optional[str] = None, region_filter: Optional[str] = None, quantity_filter: Optional[int] = None, clouds: Optional[Union[List[str], str]] = None) -> server_common.RequestId[Dict[str, List[float]]]
```

Lists all accelerators offered by Sky and available counts.

**Args:**
    gpus_only: Whether to only list GPU accelerators.
    name_filter: The name filter.
    region_filter: The region filter.
    quantity_filter: The quantity filter.
    clouds: The clouds to list.

**Returns:**
    The request ID of the list accelerator counts request.

**Request Returns:**
    acc_to_acc_num_dict (Dict[str, List[int]]): A dictionary of canonical
        accelerator names mapped to a list of available counts. See usage
        in cli.py.

### `sky.local_down`

```python
sky.local_down(name: Optional[str]) -> server_common.RequestId[None]
```

Tears down the Kubernetes cluster started by local_up.

### `sky.local_up`

```python
sky.local_up(gpus: bool, name: Optional[str] = None, port_start: Optional[int] = None) -> server_common.RequestId[None]
```

Launches a Kubernetes cluster on local machines.

**Returns:**
    request_id: The request ID of the local up request.

### `sky.realtime_slurm_gpu_availability`

```python
sky.realtime_slurm_gpu_availability(name_filter: Optional[str] = None, quantity_filter: Optional[int] = None, slurm_cluster_name: Optional[str] = None) -> server_common.RequestId
```

Gets the real-time Slurm GPU availability.

**Args:**
    name_filter: Optional name filter for GPUs.
    quantity_filter: Optional quantity filter for GPUs.
    slurm_cluster_name: Optional Slurm cluster name to filter by.

**Returns:**
    The request ID of the Slurm GPU availability request.

### `sky.slurm_node_info`

```python
sky.slurm_node_info(slurm_cluster_name: Optional[str] = None) -> server_common.RequestId
```

Gets the resource information for all nodes in the Slurm cluster.

**Returns:**
    The request ID of the Slurm node info request.

**Request Returns:**
    List[Dict[str, Any]]: A list of dictionaries, each containing info
        for a single Slurm node (node_name, partition, node_state,
        gpu_type, total_gpus, free_gpus, vcpu_count, memory_gb).

### `sky.ssh_down`

```python
sky.ssh_down(infra: Optional[str] = None) -> server_common.RequestId[None]
```

Tears down a Kubernetes cluster on SSH targets.

**Args:**
    infra: Name of the cluster configuration in ssh_targets.yaml.
        If None, the first cluster in the file is used.

**Returns:**
    request_id: The request ID of the SSH cluster teardown request.

### `sky.ssh_up`

```python
sky.ssh_up(infra: Optional[str] = None, file: Optional[str] = None) -> server_common.RequestId[None]
```

Deploys the SSH Node Pools defined in ~/.sky/ssh_targets.yaml.

**Args:**
    infra: Name of the cluster configuration in ssh_targets.yaml.
        If None, the first cluster in the file is used.
    file: Name of the ssh node pool configuration file to use. If
        None, the default path, ~/.sky/ssh_node_pools.yaml is used.

**Returns:**
    request_id: The request ID of the SSH cluster deployment request.

### `sky.status_kubernetes`

```python
sky.status_kubernetes() -> server_common.RequestId[Tuple[List['kubernetes_utils.KubernetesSkyPilotClusterInfoPayload'], List['kubernetes_utils.KubernetesSkyPilotClusterInfoPayload'], List[responses.ManagedJobRecord], Optional[str]]]
```

[Experimental] Gets all SkyPilot clusters and jobs
in the Kubernetes cluster.

Managed jobs and services are also included in the clusters returned.
The caller must parse the controllers to identify which clusters are run
as managed jobs or services.

**Returns:**
    The request ID of the status request.

**Request Returns:**
    A tuple containing:
    - all_clusters: List of KubernetesSkyPilotClusterInfoPayload with info
        for all clusters, including managed jobs, services and controllers.
    - unmanaged_clusters: List of KubernetesSkyPilotClusterInfoPayload with
        info for all clusters excluding managed jobs and services.
        Controllers are included.
    - all_jobs: List of managed jobs from all controllers. Each entry is a
        dictionary job info, see jobs.queue_from_kubernetes_pod for details.
    - context: Kubernetes context used to fetch the cluster information.

### `sky.stream_response`

```python
sky.stream_response(request_id: Optional[server_common.RequestId[T]], response: 'requests.Response', output_stream: Optional['io.TextIOBase'] = None, resumable: bool = False, get_result: bool = True) -> Optional[T]
```

Streams the response to the console.

**Args:**
    request_id: The request ID of the request to stream. May be a full
        request ID or a prefix.
        If None, the latest request submitted to the API server is streamed.
        Using None request_id is not recommended in multi-user environments.
    response: The HTTP response.
    output_stream: The output stream to write to. If None, print to the
        console.
    resumable: Whether the response is resumable on retry. If True, the
        streaming will start from the previous failure point on retry.
    get_result: Whether to get the result of the request. This will
        typically be set to False for `--no-follow` flags as requests may
        continue to run for long periods of time without further streaming.
