"""Execution layer: resource provisioner + task launcher.

Usage:

    >> sky.launch(planned_dag)

Current resource privisioners:

    - Ray autoscaler

Current task launcher:

    - ray exec + each task's commands
"""
import copy
import enum
import subprocess
import sys
import textwrap
import traceback
from typing import Any, List, Optional

import sky
from sky import backends
from sky import global_user_state
from sky import sky_logging
from sky import optimizer
from sky.data import storage
from sky.backends import backend_utils

logger = sky_logging.init_logger(__name__)

OptimizeTarget = optimizer.OptimizeTarget


class Stage(enum.Enum):
    """Stages for a run of a sky.Task."""
    # TODO: rename actual methods to be consistent.
    OPTIMIZE = 0
    PROVISION = 1
    SYNC_WORKDIR = 2
    SYNC_FILE_MOUNTS = 3
    PRE_EXEC = 4
    EXEC = 5
    TEARDOWN = 6


def _execute(dag: sky.Dag,
             dryrun: bool = False,
             teardown: bool = False,
             stream_logs: bool = True,
             handle: Any = None,
             backend: Optional[backends.Backend] = None,
             optimize_target: OptimizeTarget = OptimizeTarget.COST,
             stages: Optional[List[Stage]] = None,
             cluster_name: Optional[str] = None,
             detach_run: bool = False) -> None:
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
      optimize_target: OptimizeTarget; the dag optimization metric, e.g.
        OptimizeTarget.COST.
      stages: List of stages to run.  If None, run the whole life cycle of
        execution; otherwise, just the specified stages.  Used for skipping
        setup.
      cluster_name: Name of the cluster to create/reuse.  If None,
        auto-generate a name.
      detach_run: bool; whether to detach the process after the job submitted.
    """
    assert len(dag) == 1, 'Sky assumes 1 task for now.'
    task = dag.tasks[0]

    cluster_exists = False
    if cluster_name is not None:
        existing_handle = global_user_state.get_handle_from_cluster_name(
            cluster_name)
        cluster_exists = existing_handle is not None

    if not cluster_exists and (stages is None or Stage.OPTIMIZE in stages):
        if task.best_resources is None:
            # TODO: fix this for the situation where number of requested
            # accelerators is not an integer.
            dag = sky.optimize(dag, minimize=optimize_target)
            task = dag.tasks[0]  # Keep: dag may have been deep-copied.

    backend = backend if backend is not None else backends.CloudVmRayBackend()
    backend.register_info(dag=dag, optimize_target=optimize_target)

    if task.storage_mounts is not None:
        # Optimizer should eventually choose where to store bucket
        task.add_storage_mounts()

    status_printed = False
    try:
        if stages is None or Stage.PROVISION in stages:
            if handle is None:
                # **Dangerous**.  If passing a handle, changes to (1) setup
                # commands (2) file_mounts list/content (3) other state about
                # the cluster are IGNORED.  Careful.
                #
                # Mitigations: introduce backend.run_setup_commands() as a
                # standalone stage; fix CloudVmRayBackend.sync_file_mounts() to
                # sync all files.  They are mitigations because if one manually
                # changes the cluster AND passing in a handle, the cluster still
                # would not be updated correctly.
                handle = backend.provision(task,
                                           task.best_resources,
                                           dryrun=dryrun,
                                           stream_logs=stream_logs,
                                           cluster_name=cluster_name)

        if dryrun:
            logger.info('Dry run finished.')
            return

        if stages is None or Stage.SYNC_WORKDIR in stages:
            if task.workdir is not None:
                backend.sync_workdir(handle, task.workdir)

        if stages is None or Stage.SYNC_FILE_MOUNTS in stages:
            backend.sync_file_mounts(handle, task.file_mounts,
                                     task.get_cloud_to_remote_file_mounts())

        if stages is None or Stage.PRE_EXEC in stages:
            backend.run_post_setup(handle, task.post_setup_fn, task)

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
                backend.teardown(handle)
    except Exception:  # pylint: disable=broad-except
        # UX: print live clusters to make users aware (to save costs).
        # Shorter stacktrace than raise e (e.g., no cli stuff).
        traceback.print_exc()
        print()
        backends.backend_utils.run('sky status')
        status_printed = True
        sys.exit(1)
    finally:
        if not status_printed:
            # Needed because this finally doesn't always get executed on errors.
            backends.backend_utils.run('sky status')


def launch(dag: sky.Dag,
           dryrun: bool = False,
           teardown: bool = False,
           stream_logs: bool = True,
           backend: Optional[backends.Backend] = None,
           optimize_target: OptimizeTarget = OptimizeTarget.COST,
           cluster_name: Optional[str] = None,
           detach_run: bool = False) -> None:
    _execute(dag=dag,
             dryrun=dryrun,
             teardown=teardown,
             stream_logs=stream_logs,
             handle=None,
             backend=backend,
             optimize_target=optimize_target,
             cluster_name=cluster_name,
             detach_run=detach_run)


def exec(  # pylint: disable=redefined-builtin
    dag: sky.Dag,
    dryrun: bool = False,
    teardown: bool = False,
    stream_logs: bool = True,
    backend: Optional[backends.Backend] = None,
    optimize_target: OptimizeTarget = OptimizeTarget.COST,
    cluster_name: Optional[str] = None,
    detach_run: bool = False,
) -> None:
    handle = global_user_state.get_handle_from_cluster_name(cluster_name)
    if handle is None:
        raise ValueError(f'Cluster \'{cluster_name}\' not found.  '
                         'Use `sky launch` to provision first.')
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


def _verify_chain_dag(dag):
    graph = dag.get_graph()
    tasks = list(dag.get_sorted_tasks())
    for task_i, task in enumerate(tasks):
        successors = list(graph.successors(task))
        if task_i != len(tasks) - 1:
            if len(successors) != 1:
                raise ValueError('Dag should be a chain of tasks. Task '
                                 f' {task} has more than one successor.')
        elif len(successors) != 0:
            raise ValueError('Dag should be a chain of tasks. The lat task '
                             f' {task} has successors.')


def launch_chain(dag: sky.Dag,
                 dryrun: bool = False,
                 teardown: bool = False,
                 stream_logs: bool = True,
                 backend: Optional[backends.Backend] = None,
                 optimize_target: OptimizeTarget = OptimizeTarget.COST,
                 cluster_name: Optional[str] = None,
                 detach_run: bool = False) -> None:
    logger.warning(
        '`launch_chain` is still experimental and may change in the future. '
        'Dag should be a chain of tasks.')
    if len(dag) == 1:
        return sky.launch(dag, dryrun, teardown, stream_logs, backend,
                          optimize_target, cluster_name, detach_run)

    _verify_chain_dag(dag)

    cluster_name = backend_utils.generate_cluster_name()
    sky.optimize(dag, minimize=optimize_target)
    dag = copy.deepcopy(dag)
    tasks = list(dag.get_sorted_tasks())
    for i, task in enumerate(tasks):
        task_cluster_name = '-'.join(
            [cluster_name, task.name.replace('_', '-'),
             str(i)])
        name = storage.get_storage_name(task.get_inputs(), task_cluster_name)
        cloud = task.best_resources.cloud
        current_storage_type = storage.get_storage_type_from_cloud(cloud)

        # Find the correct input storage type by task running cloud
        input_storage = sky.Storage(name=name, source=task.get_inputs())
        input_storage.add_store(current_storage_type)

        input_mount_path = f'~/.sky/sky-task-{i}-inputs'
        task.set_storage_mounts({
            input_storage: input_mount_path,
        })

        task.run = task.run.replace('INPUTS[0]', f'{input_mount_path}')

        output_path = f'~/.sky/sky-task-{i}-outputs'
        task.run = task.run.replace('OUTPUTS[0]', output_path)

        # Execute the task on the cloud
        task.set_resources(task.best_resources)
        with sky.Dag() as task_dag:
            task_dag.add(task)
        sky.launch(task_dag, cluster_name=task_cluster_name)

        # Upload the outputs to the correct storage
        next_storage_path = copy.copy(task.get_outputs())
        if next_storage_path is not None:
            next_storage_name = storage.get_storage_name(
                next_storage_path, None)
            sky_storage_codegen = (
                f'import sky; sky.Storage(name={next_storage_name!r}, '
                f'source={output_path!r}).add_store('
                f'sky.StorageType[{current_storage_type.name!r}])')
            if next_storage_path.startswith('CLOUD://'):
                assert i < len(tasks) - 1
                next_task = tasks[i + 1]
                next_cloud = next_task.best_resources.cloud
                next_storage_type = storage.get_storage_type_from_cloud(
                    next_cloud)

                next_task.inputs = next_task.inputs.replace(
                    'CLOUD://', next_storage_type.value)
                sky_storage_codegen += (
                    '.add_store('
                    f'sky.StorageType[{next_storage_type.name!r}])')
            upload_code_gen = textwrap.dedent(f"""\
                pip3 install -U boto3
                python3 -u -c {sky_storage_codegen!r}
                """)

            with sky.Dag() as upload_dag:
                sky.Task(f'upload-{i}', run=upload_code_gen)
            sky.exec(upload_dag, cluster_name=task_cluster_name)
        proc = subprocess.Popen(f'sky down {task_cluster_name}', shell=True)
    proc.wait()
