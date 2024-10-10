from collections.abc import Callable
import functools
from inspect import Parameter
from inspect import signature
from pathlib import Path
from platform import python_version
from tempfile import NamedTemporaryFile
from textwrap import dedent
from typing import Any, Dict, List, Optional, Union

import cloudpickle

from sky import backends
from sky import optimizer
from sky import sky_logging
from sky.backends import backend_utils
from sky.execution import _execute
from sky.execution import Stage
from sky.task import Task
from sky.utils import controller_utils

logger = sky_logging.init_logger(__name__)


def _merge_default_kwargs(func, kwargs):
    sig = signature(func)
    if sig.parameters:
        kwarg_defaults = {
            name: param.default
            for (name, param) in sig.parameters.items()
            if param.default != Parameter.empty
        }

    return kwarg_defaults | kwargs


def _wrapped_to_script(func: Callable, args: List[Any], kwargs: Dict[str, Any],
                       output_file: Path) -> Path:
    kwargs = _merge_default_kwargs(func, kwargs)

    pickled_func = cloudpickle.dumps(func)
    pickled_args = cloudpickle.dumps(args)
    pickled_kwargs = cloudpickle.dumps(kwargs)

    script = dedent(f"""
        import pickle
        from platform import python_version

        host_python = "{python_version()}"
        cluster_python = python_version()
        if host_python != cluster_python:
          raise ValueError(
            f"Host python version {{host_python}} does not match the cluster python version {{cluster_python}}"
          )

        func = pickle.loads({pickled_func})
        args = pickle.loads({pickled_args})
        kwargs = pickle.loads({pickled_kwargs})

        func(*args, **kwargs)
        """)

    with open(output_file, "w") as f:
        f.write(script)

    return Path(output_file)


def sky_task(
    task: Union[Task, str, Path],
    cluster_name: Optional[str] = None,
    retry_until_up: bool = False,
    idle_minutes_to_autostop: Optional[int] = None,
    dryrun: bool = False,
    down: bool = False,
    stream_logs: bool = True,
    backend: Optional[backends.Backend] = None,
    optimize_target: optimizer.OptimizeTarget = optimizer.OptimizeTarget.COST,
    detach_setup: bool = False,
    detach_run: bool = False,
    no_setup: bool = False,
    clone_disk_from: Optional[str] = None,
):
    """
    This is EXPERIMENTAL.

    Run a function as a Sky task. If a cluster_name is provided and already exists, the task will be executed on it.
    Otherwise, a new cluster will be created.

    The wrapped functions return value will be ignored. To return data from the task, write it to a cloud storage
    system for retrieval.
    """

    def _decorator(func: Callable):

        @functools.wraps(func)
        def _sky_task(*args, **kwargs):
            if isinstance(task, Task):
                base_task = task
            elif isinstance(task, (Path, str)):
                base_task = Task.from_yaml(str(task))
            else:
                raise ValueError(
                    f"task must be a str, Path, or sky.Task object. Got {type(task)}"
                )

            with NamedTemporaryFile() as tempfile:
                script_file = _wrapped_to_script(func, args, kwargs,
                                                 Path(tempfile.name))
                base_task.update_file_mounts(
                    {"/tmp/sky_tasks/script.py": str(script_file.absolute())})

                base_task.run = "python /tmp/sky_tasks/script.py"

                entrypoint = base_task

                controller_utils.check_cluster_name_not_controller(
                    cluster_name, operation_str='sky.exec')
                if cluster_name:
                    try:
                        handle = backend_utils.check_cluster_available(
                            cluster_name,
                            operation='executing tasks',
                            check_cloud_vm_ray_backend=False,
                            dryrun=dryrun)
                        return _execute(
                            entrypoint=entrypoint,
                            dryrun=dryrun,
                            down=down,
                            stream_logs=stream_logs,
                            handle=handle,
                            backend=backend,
                            stages=[
                                Stage.SYNC_WORKDIR,
                                Stage.SYNC_FILE_MOUNTS,
                                Stage.EXEC,
                            ],
                            cluster_name=cluster_name,
                            detach_run=detach_run,
                        )
                    except ValueError:
                        logger.info(
                            f"Cluster {cluster_name} not found. Creating a new cluster."
                        )

                _execute(
                    entrypoint=entrypoint,
                    dryrun=dryrun,
                    down=down,
                    stream_logs=stream_logs,
                    handle=None,
                    backend=backend,
                    retry_until_up=retry_until_up,
                    optimize_target=optimize_target,
                    cluster_name=cluster_name,
                    detach_setup=detach_setup,
                    detach_run=detach_run,
                    idle_minutes_to_autostop=idle_minutes_to_autostop,
                    no_setup=no_setup,
                    clone_disk_from=clone_disk_from,
                )

        return _sky_task

    return _decorator
