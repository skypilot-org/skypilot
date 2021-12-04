"""Execution layer: resource provisioner + task launcher.

Usage:

   >> sky.execute(planned_dag)

Current resource privisioners:

  - Ray autoscaler

Current task launcher:

  - ray exec + each task's commands
"""
from typing import Any, Optional

import sky
from sky import backends
from sky import logging
from sky import optimizer

logger = logging.init_logger(__name__)

OptimizeTarget = optimizer.OptimizeTarget


def execute_v2(dag: sky.Dag,
               dryrun: bool = False,
               teardown: bool = False,
               stream_logs: bool = True,
               handle: Any = None,
               backend: Optional[backends.Backend] = None,
               optimize_target: OptimizeTarget = OptimizeTarget.COST) -> None:
    """Executes a planned DAG.

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
    """
    # TODO: Azure.
    assert len(dag) == 1, 'Sky assumes 1 task for now.'
    task = dag.tasks[0]
    if task.best_resources is None:
        logger.info(f'Optimizer target is set to {optimize_target.name}.')
        dag = sky.optimize(dag, minimize=optimize_target)
    best_resources = task.best_resources

    backend = backend if backend is not None else backends.CloudVmRayBackend()
    backend.register_info(dag=dag, optimize_target=optimize_target)

    if handle is None:
        handle = backend.provision(task,
                                   best_resources,
                                   dryrun=dryrun,
                                   stream_logs=stream_logs)

    if dryrun:
        logger.info('Dry run finished.')
        return

    if task.workdir is not None:
        backend.sync_workdir(handle, task.workdir)

    backend.sync_file_mounts(handle, task.file_mounts,
                             task.get_cloud_to_remote_file_mounts())

    if task.post_setup_fn is not None:
        backend.run_post_setup(handle, task.post_setup_fn, task)

    try:
        backend.execute(handle, task, stream_logs)
    finally:
        # Enables post_execute() to be run after KeyboardInterrupt.
        backend.post_execute(handle, teardown)

    if teardown:
        backend.teardown(handle)


execute = execute_v2
