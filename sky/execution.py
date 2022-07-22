"""Execution layer: resource provisioner + task launcher.

Usage:

   >> sky.launch(planned_dag)

Current resource privisioners:

  - Ray autoscaler

Current task launcher:

  - ray exec + each task's commands
"""
import enum
import os
import time
from typing import Any, List, Optional

import sky
from sky import backends
from sky import global_user_state
from sky import optimizer
from sky import sky_logging
from sky import spot
from sky.backends import backend_utils
from sky.usage import usage_lib
from sky.utils import env_options, timeline
from sky.utils import subprocess_utils
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

OptimizeTarget = optimizer.OptimizeTarget


class Stage(enum.Enum):
    """Stages for a run of a sky.Task."""
    # TODO: rename actual methods to be consistent.
    OPTIMIZE = enum.auto()
    PROVISION = enum.auto()
    SYNC_WORKDIR = enum.auto()
    SYNC_FILE_MOUNTS = enum.auto()
    SETUP = enum.auto()
    PRE_EXEC = enum.auto()
    EXEC = enum.auto()
    TEARDOWN = enum.auto()


def _execute(
    dag: sky.Dag,
    dryrun: bool = False,
    teardown: bool = False,
    stream_logs: bool = True,
    handle: Any = None,
    backend: Optional[backends.Backend] = None,
    retry_until_up: bool = False,
    optimize_target: OptimizeTarget = OptimizeTarget.COST,
    stages: Optional[List[Stage]] = None,
    cluster_name: Optional[str] = None,
    detach_run: bool = False,
    idle_minutes_to_autostop: Optional[int] = None,
) -> None:
    """Runs a DAG.

    If the DAG has not been optimized yet, this will call sky.optimize() for
    the caller.

    Args:
      dag: sky.Dag.
      dryrun: bool; if True, only print the provision info (e.g., cluster
        yaml).
      teardown: bool; whether to teardown the launched resources after
        execution.
      stream_logs: bool; whether to stream all tasks' outputs to the client.
      handle: Any; if provided, execution will use an existing backend cluster
        handle instead of provisioning a new one.
      backend: Backend; backend to use for executing the tasks. Defaults to
        CloudVmRayBackend()
      retry_until_up: bool; whether to retry the provisioning until the cluster
        is up.
      optimize_target: OptimizeTarget; the dag optimization metric, e.g.
        OptimizeTarget.COST.
      stages: List of stages to run.  If None, run the whole life cycle of
        execution; otherwise, just the specified stages.  Used for `sky exec`
        skipping all setup steps.
      cluster_name: Name of the cluster to create/reuse.  If None,
        auto-generate a name.
      detach_run: bool; whether to detach the process after the job submitted.
      autostop_idle_minutes: int; if provided, the cluster will be set to
        autostop after this many minutes of idleness.
    """
    assert len(dag) == 1, f'We support 1 task for now. {dag}'
    task = dag.tasks[0]

    if task.need_spot_recovery:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                'Spot recovery is specified in the task. To launch the '
                'managed spot job, please use: sky spot launch')

    cluster_exists = False
    if cluster_name is not None:
        existing_handle = global_user_state.get_handle_from_cluster_name(
            cluster_name)
        cluster_exists = existing_handle is not None

    backend = backend if backend is not None else backends.CloudVmRayBackend()
    if not isinstance(backend, backends.CloudVmRayBackend
                     ) and idle_minutes_to_autostop is not None:
        # TODO(zhwu): Autostop is not supported for non-CloudVmRayBackend.
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                f'Backend {backend.NAME} does not support autostop, please try '
                f'{backends.CloudVmRayBackend.NAME}')

    if not cluster_exists and (stages is None or Stage.OPTIMIZE in stages):
        if task.best_resources is None:
            # TODO: fix this for the situation where number of requested
            # accelerators is not an integer.
            if isinstance(backend, backends.CloudVmRayBackend):
                # TODO: adding this check because docker backend on a
                # no-credential machine should not enter optimize(), which
                # would directly error out ('No cloud is enabled...').  Fix by
                # moving `sky check` checks out of optimize()?
                dag = sky.optimize(dag, minimize=optimize_target)
                task = dag.tasks[0]  # Keep: dag may have been deep-copied.
                assert task.best_resources is not None, task

    backend.register_info(dag=dag, optimize_target=optimize_target)

    if task.storage_mounts is not None:
        # Optimizer should eventually choose where to store bucket
        task.add_storage_mounts()

    try:
        if stages is None or Stage.PROVISION in stages:
            if handle is None:
                handle = backend.provision(task,
                                           task.best_resources,
                                           dryrun=dryrun,
                                           stream_logs=stream_logs,
                                           cluster_name=cluster_name,
                                           retry_until_up=retry_until_up)

        if dryrun:
            logger.info('Dry run finished.')
            return

        if stages is None or Stage.SYNC_WORKDIR in stages:
            if task.workdir is not None:
                backend.sync_workdir(handle, task.workdir)

        if stages is None or Stage.SYNC_FILE_MOUNTS in stages:
            backend.sync_file_mounts(handle, task.file_mounts,
                                     task.storage_mounts)

        if stages is None or Stage.SETUP in stages:
            backend.setup(handle, task)

        if stages is None or Stage.PRE_EXEC in stages:
            if idle_minutes_to_autostop is not None:
                backend.set_autostop(handle, idle_minutes_to_autostop)

        if stages is None or Stage.EXEC in stages:
            try:
                global_user_state.update_last_use(handle.get_cluster_name())
                backend.execute(handle, task, detach_run)
            finally:
                # Enables post_execute() to be run after KeyboardInterrupt.
                backend.post_execute(handle, teardown)

        if stages is None or Stage.TEARDOWN in stages:
            if teardown:
                backend.teardown_ephemeral_storage(task)
                backend.teardown(handle, terminate=True)
    finally:
        # UX: print live clusters to make users aware (to save costs).
        # Needed because this finally doesn't always get executed on errors.
        # Disable the usage collection for this status command.
        env = dict(os.environ,
                   **{env_options.Options.DISABLE_LOGGING.value: '1'})
        if cluster_name == spot.SPOT_CONTROLLER_NAME:
            # For spot controller task, it requires a while to have the
            # managed spot status shown in the status table.
            time.sleep(0.5)
            subprocess_utils.run('sky spot status', env=env)
        else:
            subprocess_utils.run('sky status', env=env)
        print()
        print('\x1b[?25h', end='')  # Show cursor.


@timeline.event
@usage_lib.entrypoint
def launch(
    dag: sky.Dag,
    dryrun: bool = False,
    teardown: bool = False,
    stream_logs: bool = True,
    backend: Optional[backends.Backend] = None,
    retry_until_up: bool = False,
    optimize_target: OptimizeTarget = OptimizeTarget.COST,
    cluster_name: Optional[str] = None,
    detach_run: bool = False,
    idle_minutes_to_autostop: Optional[int] = None,
    is_spot_controller_task: bool = False,
) -> None:
    if not is_spot_controller_task:
        backend_utils.check_cluster_name_not_reserved(
            cluster_name, operation_str='sky.launch')
    _execute(
        dag=dag,
        dryrun=dryrun,
        teardown=teardown,
        stream_logs=stream_logs,
        handle=None,
        backend=backend,
        retry_until_up=retry_until_up,
        optimize_target=optimize_target,
        cluster_name=cluster_name,
        detach_run=detach_run,
        idle_minutes_to_autostop=idle_minutes_to_autostop,
    )


@usage_lib.entrypoint
def exec(  # pylint: disable=redefined-builtin
    dag: sky.Dag,
    cluster_name: str,
    dryrun: bool = False,
    teardown: bool = False,
    stream_logs: bool = True,
    backend: Optional[backends.Backend] = None,
    optimize_target: OptimizeTarget = OptimizeTarget.COST,
    detach_run: bool = False,
) -> None:
    backend_utils.check_cluster_name_not_reserved(cluster_name,
                                                  operation_str='sky.exec')

    status, handle = backend_utils.refresh_cluster_status_handle(cluster_name)
    with ux_utils.print_exception_no_traceback():
        if handle is None:
            raise ValueError(f'Cluster {cluster_name!r} not found.  '
                             'Use `sky launch` to provision first.')
        if status != global_user_state.ClusterStatus.UP:
            raise ValueError(f'Cluster {cluster_name!r} is not up.  '
                             'Use `sky status` to check the status.')
    _execute(dag=dag,
             dryrun=dryrun,
             teardown=teardown,
             stream_logs=stream_logs,
             handle=handle,
             backend=backend,
             optimize_target=optimize_target,
             stages=[
                 Stage.SYNC_WORKDIR,
                 Stage.EXEC,
             ],
             cluster_name=cluster_name,
             detach_run=detach_run)
