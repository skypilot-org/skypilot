"""Execution layer: resource provisioner + task launcher.

Usage:

   >> planned_dag = sky.optimize(dag)
   >> sky.execute(planned_dag)

Current resource privisioners:

  - Ray autoscaler

Current task launcher:

  - ray exec + each task's commands
"""
from typing import Callable, Dict, List, Union, Optional

import sky
from sky import logging
from sky import backends
from sky.backends import backend_utils

logger = logging.init_logger(__name__)

IPAddr = str
ShellCommand = str
ShellCommandGenerator = Callable[[List[IPAddr]], Dict[IPAddr, ShellCommand]]
ShellCommandOrGenerator = Union[ShellCommand, ShellCommandGenerator]

SKY_LOGS_DIRECTORY = './logs'
STREAM_LOGS_TO_CONSOLE = True

App = backend_utils.App
RunId = backend_utils.RunId

SKY_REMOTE_WORKDIR = backend_utils.SKY_REMOTE_WORKDIR


def execute_v2(dag: sky.Dag,
               dryrun: bool = False,
               teardown: bool = False,
               stream_logs: bool = True,
               backend: Optional[backends.Backend] = None) -> None:
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
      backend: Backend; backend to use for executing the tasks. Defaults to
        CloudVmRayBackend()
    """
    # TODO: Azure. Port some of execute_v1()'s nice logging messages.
    assert len(dag) == 1, 'Job launcher assumes 1 task for now.'
    task = dag.tasks[0]
    best_resources = task.best_resources
    assert best_resources is not None, \
        'Run sky.Optimize.optimize() before sky.execute().'

    backend = backend if backend is not None else backends.CloudVmRayBackend()

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
