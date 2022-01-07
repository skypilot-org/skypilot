"""Execution layer: resource provisioner + task launcher.

Usage:

   >> sky.execute(planned_dag)

Current resource privisioners:

  - Ray autoscaler

Current task launcher:

  - ray exec + each task's commands
"""
import enum
import sys
import traceback
from typing import Any, List, Optional

import sky
from sky import backends
from sky import logging
from sky import optimizer

logger = logging.init_logger(__name__)

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


def execute(dag: sky.Dag,
            dryrun: bool = False,
            teardown: bool = False,
            stream_logs: bool = True,
            handle: Any = None,
            backend: Optional[backends.Backend] = None,
            optimize_target: OptimizeTarget = OptimizeTarget.COST,
            stages: Optional[List[Stage]] = None,
            cluster_name: Optional[str] = None,
            detach: bool = False) -> None:
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
        Hint: for a ParTask, set this to False to avoid a lot of log outputs;
        each task's output can be redirected to their own files.
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
    """
    assert len(dag) == 1, 'Sky assumes 1 task for now.'
    task = dag.tasks[0]

    if stages is None or Stage.OPTIMIZE in stages:
        if task.best_resources is None:
            logger.info(f'Optimizer target is set to {optimize_target.name}.')
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
            if task.post_setup_fn is not None:
                backend.run_post_setup(handle, task.post_setup_fn, task)

        if stages is None or Stage.EXEC in stages:
            try:
                backend.execute(handle, task, stream_logs, detach)
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
