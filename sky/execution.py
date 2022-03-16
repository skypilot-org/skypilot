"""Execution layer: resource provisioner + task launcher.

Usage:

   >> sky.launch(planned_dag)

Current resource privisioners:

  - Ray autoscaler

Current task launcher:

  - ray exec + each task's commands
"""
import enum
import sys
import traceback
from typing import Any, List, Optional
import ray
import time

import sky
from sky import backends
from sky import global_user_state
from sky import sky_logging
from sky import optimizer
from sky import task as task_lib
from sky.backends import backend_utils

Task = task_lib.Task

logger = sky_logging.init_logger(__name__)

OptimizeTarget = optimizer.OptimizeTarget


class Stage(enum.Enum):
    """Stages for a run of a sky.Task."""
    # TODO: rename actual methods to be consistent.
    OPTIMIZE = 0
    PROVISION = 1
    SYNC_WORKDIR = 2
    SYNC_FILE_MOUNTS = 3
    SETUP = 4
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
        execution; otherwise, just the specified stages.  Used for `sky exec`
        skipping all setup steps.
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

    backend = backend if backend is not None else backends.CloudVmRayBackend()

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

    backend.register_info(dag=dag, optimize_target=optimize_target)

    if task.storage_mounts is not None:
        # Optimizer should eventually choose where to store bucket
        task.add_storage_mounts()

    status_printed = False
    try:
        if stages is None or Stage.PROVISION in stages:
            if handle is None:
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

        if stages is None or Stage.SETUP in stages:
            backend.setup(handle, task)

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


@ray.remote
class Executor:
    #TODO: Add docstring
    """Executor."""

    def __init__(self, actor_name):
        self.actor_name = actor_name

    def execute_one_task(self,
                         task: Task,
                         dryrun: bool = False,
                         teardown: bool = False,
                         stream_logs: bool = True,
                         handle: Any = None,
                         backend: Optional[backends.Backend] = None,
                         stages: Optional[List[Stage]] = None,
                         cluster_name: Optional[str] = None,
                         detach_run: bool = False) -> None:
        """Runs a Task.

        Args:
          dag: sky.Dag.
          dryrun: bool; if True, only print the provision info (e.g., cluster
            yaml).
          teardown: bool; whether to teardown the launched resources after
            execution.
          stream_logs: bool; whether to stream all tasks' outputs to the client.
          handle: Any; if provided, execution will use an existing backend
            cluster handle instead of provisioning a new one.
          backend: Backend; backend to use for executing the tasks. Defaults to
            CloudVmRayBackend()
          stages: List of stages to run. If None, run the whole life cycle of
            execution; otherwise, just the specified stages. Used for `sky exec`
            skipping all setup steps.
          cluster_name: Name of the cluster to create/reuse. If None,
            auto-generate a name.
          detach_run: bool; whether to detach the process after the job
            submitted.
        """
        backend = backends.CloudVmRayBackend() if backend is None else backend

        if task.storage_mounts is not None:
            # Optimizer should eventually choose where to store bucket
            task.add_storage_mounts()

        status_printed = False
        try:
            if stages is None or Stage.PROVISION in stages:
                if handle is None:
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

            if stages is None or Stage.SETUP in stages:
                backend.setup(handle, task)

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
                # Needed because this finally doesn't always get executed
                # on errors.
                backends.backend_utils.run('sky status')


def launch_multitask(dag: sky.Dag,
                     dryrun: bool = False,
                     teardown: bool = False,
                     stream_logs: bool = True,
                     backend: Optional[backends.Backend] = None,
                     optimize_target: OptimizeTarget = OptimizeTarget.COST,
                     cluster_name: Optional[str] = None,
                     detach_run: bool = False) -> None:

    assert cluster_name is None, 'cluster reuse not supported yet'

    for node in dag.graph.nodes:
        assert dag.graph.in_degree(
            node) == 0, 'Assume multi tasks are independent now'
    # cluster_exists = False
    # if cluster_name is not None:
    #     existing_handle = global_user_state.get_handle_from_cluster_name(
    #         cluster_name)
    #     cluster_exists = existing_handle is not None
    # print('[debug]', 'cluster_exists', cluster_exists)

    backend = backend if backend is not None else backends.CloudVmRayBackend()
    task = dag.tasks[0]

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

    backend.register_info(dag=dag, optimize_target=optimize_target)
    if cluster_name is None:
        cluster_name = backend_utils.generate_cluster_name()

    ret_list = []
    for idx, task in enumerate(dag.tasks):
        sub_cluster_name = cluster_name + f'-{idx}'
        executor = Executor.options(
            name=sub_cluster_name).remote(sub_cluster_name)
        ret_list.append(
            executor.execute_one_task.remote(task=task,
                                             dryrun=dryrun,
                                             teardown=teardown,
                                             stream_logs=stream_logs,
                                             handle=None,
                                             backend=backend,
                                             cluster_name=sub_cluster_name,
                                             detach_run=detach_run))
        # FIXME: is this needed?
        time.sleep(2)

    not_ready = ret_list
    while not_ready:
        ready, not_ready = ray.wait(not_ready, num_returns=1)
        try:
            ray.get(ready)
        except ray.exceptions.RayActorError as e:
            logger.info(e)

    # TODO: Fix the broken output messages
    # TODO: Hint users how to teardown clusters spawned by multi-task
    # TODO: Organize output messages


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
