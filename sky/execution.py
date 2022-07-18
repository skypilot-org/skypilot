"""Execution layer: resource provisioner + task launcher.

Usage:

   >> sky.launch(planned_dag)

Current resource privisioners:

  - Ray autoscaler

Current task launcher:

  - ray exec + each task's commands
"""
import enum
import tempfile
import time
from typing import Any, List, Optional

import colorama

import sky
from sky import backends
from sky import exceptions
from sky import global_user_state
from sky import optimizer
from sky import sky_logging
from sky import spot
from sky.backends import backend_utils
from sky.data import data_utils
from sky.data import storage
from sky.utils import subprocess_utils
from sky.utils import timeline
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

OptimizeTarget = optimizer.OptimizeTarget
_MAX_SPOT_JOB_LENGTH = 10


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
    assert len(dag) == 1, f'Sky assumes 1 task for now. {dag}'
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
        if cluster_name == spot.SPOT_CONTROLLER_NAME:
            # For spot controller task, it requires a while to have the
            # managed spot status shown in the status table.
            time.sleep(0.5)
            subprocess_utils.run(
                f'sky spot status | head -n {_MAX_SPOT_JOB_LENGTH}')
        else:
            subprocess_utils.run('sky status')
        print()
        print('\x1b[?25h', end='')  # Show cursor.


@timeline.event
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


def spot_launch(
    dag: sky.Dag,
    name: Optional[str] = None,
    detach_run: bool = False,
) -> None:
    """Launch a managed spot job.

    Please refer to the sky.cli.spot_launch for the document.

    Args:
        dag: DAG to launch.
        name: Name of the spot job.
        detach_run: Whether to detach the run.

    Raises:
        ValueError: cluster does not exist.
        sky.exceptions.NotSupportedError: the feature is not supported.
    """
    if name is None:
        name = backend_utils.generate_cluster_name()

    assert len(dag.tasks) == 1, ('Only one task is allowed in a spot launch.',
                                 dag)
    task = dag.tasks[0]
    assert len(task.resources) == 1, task
    resources = list(task.resources)[0]

    change_default_value = dict()
    if not resources.use_spot_specified:
        logger.info('Field use_spot not specified; defaulting to True.')
        change_default_value['use_spot'] = True
    if resources.spot_recovery is None:
        logger.info('No spot recovery strategy specified; defaulting to '
                    f'{spot.SPOT_DEFAULT_STRATEGY}.')
        change_default_value['spot_recovery'] = spot.SPOT_DEFAULT_STRATEGY

    new_resources = resources.copy(**change_default_value)
    task.set_resources({new_resources})

    if task.run is None:
        logger.info(
            f'{colorama.Fore.GREEN}'
            'Skipping the managed spot task as the run section is not set.'
            f'{colorama.Style.RESET_ALL}')
        return

    # TODO(zhwu): Refactor the Task (as Resources), so that we can enforce the
    # following validations.
    # Check the file mounts in the task.
    # Disallow all local file mounts (copy mounts).
    if task.workdir is not None:
        raise exceptions.NotSupportedError(
            'Workdir is not allowed for managed spot jobs.')
    copy_mounts = task.get_local_to_remote_file_mounts()
    if copy_mounts:
        copy_mounts_str = '\n\t'.join(': '.join(m) for m in copy_mounts)
        raise exceptions.NotSupportedError(
            'Local file mounts are not allowed for managed spot jobs, '
            f'but following are found: {copy_mounts_str}')

    # Copy the local source to a bucket. The task will not be executed locally,
    # so we need to copy the files to the bucket manually here before sending to
    # the remote spot controller.
    task.add_storage_mounts()

    # Replace the source field that is local path in all storage_mounts with
    # bucket URI and remove the name field.
    for storage_obj in task.storage_mounts.values():
        if (storage_obj.source is not None and
                not data_utils.is_cloud_store_url(storage_obj.source)):
            # Need to replace the local path with bucket URI, and remove the
            # name field, so that the sky storage mount can work on the spot
            # controller.
            store_types = list(storage_obj.stores.keys())
            assert len(store_types) == 1, (
                'We only support one store type for now.', storage_obj.stores)
            store_type = store_types[0]
            if store_type == storage.StoreType.S3:
                storage_obj.source = f's3://{storage_obj.name}'
            elif store_type == storage.StoreType.GCS:
                storage_obj.source = f'gs://{storage_obj.name}'
            else:
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.NotSupportedError(
                        f'Unsupported store type: {store_type}')
            storage_obj.name = None

    with tempfile.NamedTemporaryFile(prefix=f'sky-spot-task-{name}-',
                                     mode='w') as f:
        task_config = task.to_yaml_config()
        backend_utils.dump_yaml(f.name, task_config)

        controller_name = spot.SPOT_CONTROLLER_NAME
        yaml_path = backend_utils.fill_template(
            spot.SPOT_CONTROLLER_TEMPLATE, {
                'remote_user_yaml_prefix': spot.SPOT_TASK_YAML_PREFIX,
                'user_yaml_path': f.name,
                'spot_controller': controller_name,
                'cluster_name': name,
                'sky_remote_path': backend_utils.SKY_REMOTE_PATH,
            },
            output_prefix=spot.SPOT_CONTROLLER_YAML_PREFIX)
        with sky.Dag() as spot_dag:
            controller_task = sky.Task.from_yaml(yaml_path)
            controller_task.spot_task = task
            assert len(controller_task.resources) == 1
        logger.info(f'{colorama.Fore.YELLOW}'
                    f'Launching managed spot job {name} from spot controller...'
                    f'{colorama.Style.RESET_ALL}')
        logger.info('Launching spot controller...')
        sky.launch(spot_dag,
                   stream_logs=True,
                   cluster_name=controller_name,
                   detach_run=detach_run,
                   idle_minutes_to_autostop=spot.
                   SPOT_CONTROLLER_IDLE_MINUTES_TO_AUTOSTOP,
                   is_spot_controller_task=True)
