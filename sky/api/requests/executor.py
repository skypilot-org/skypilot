"""Executor for the requests."""
import multiprocessing
import os
import sys
import traceback
from typing import Any, Callable, Dict

from sky import sky_logging
from sky.api.requests import tasks
from sky.usage import usage_lib
from sky.utils import common
from sky.utils import ux_utils

# pylint: disable=ungrouped-imports
if sys.version_info >= (3, 10):
    from typing import ParamSpec
else:
    from typing_extensions import ParamSpec

P = ParamSpec('P')

logger = sky_logging.init_logger(__name__)


def _wrapper(func: Callable[P, Any], request_id: str, env_vars: Dict[str, str],
             ignore_return_value: bool, *args: P.args, **kwargs: P.kwargs):
    """Wrapper for a request task."""

    def redirect_output(file):
        """Redirect stdout and stderr to the log file."""
        fd = file.fileno()  # Get the file descriptor from the file object
        # Store copies of the original stdout and stderr file descriptors
        original_stdout = os.dup(sys.stdout.fileno())
        original_stderr = os.dup(sys.stderr.fileno())

        # Copy this fd to stdout and stderr
        os.dup2(fd, sys.stdout.fileno())
        os.dup2(fd, sys.stderr.fileno())
        return original_stdout, original_stderr

    def restore_output(original_stdout, original_stderr):
        """Restore stdout and stderr to their original file descriptors. """
        os.dup2(original_stdout, sys.stdout.fileno())
        os.dup2(original_stderr, sys.stderr.fileno())

        # Close the duplicate file descriptors
        os.close(original_stdout)
        os.close(original_stderr)

    pid = multiprocessing.current_process().pid
    logger.info(f'Running task {request_id} with pid {pid}')
    with tasks.update_rest_task(request_id) as request_task:
        assert request_task is not None, request_id
        log_path = request_task.log_path
        request_task.pid = pid
        request_task.status = tasks.RequestStatus.RUNNING
    with log_path.open('w', encoding='utf-8') as f:
        # Store copies of the original stdout and stderr file descriptors
        original_stdout, original_stderr = redirect_output(f)
        try:
            os.environ.update(env_vars)
            # Force color to be enabled.
            os.environ['CLICOLOR_FORCE'] = '1'
            common.reload()
            return_value = func(*args, **kwargs)
        except Exception as e:  # pylint: disable=broad-except
            with ux_utils.enable_traceback():
                stacktrace = traceback.format_exc()
            setattr(e, 'stacktrace', stacktrace)
            usage_lib.store_exception(e)
            with tasks.update_rest_task(request_id) as request_task:
                assert request_task is not None, request_id
                request_task.status = tasks.RequestStatus.FAILED
                request_task.set_error(e)
            restore_output(original_stdout, original_stderr)
            logger.info(f'Task {request_id} failed due to {e}')
            return None
        else:
            with tasks.update_rest_task(request_id) as request_task:
                assert request_task is not None, request_id
                request_task.status = tasks.RequestStatus.SUCCEEDED
                if not ignore_return_value:
                    request_task.set_return_value(return_value)
            restore_output(original_stdout, original_stderr)
            logger.info(f'Task {request_id} finished')
        return return_value


def start_background_request(
        request_id: str,
        request_name: str,
        request_body: Dict[str, Any],
        func: Callable[P, Any],
        ignore_return_value: bool = False,
        # pylint: disable=keyword-arg-before-vararg
        *args: P.args,
        **kwargs: P.kwargs):
    """Start a task."""
    request_task = tasks.RequestTask(request_id=request_id,
                                     name=request_name,
                                     entrypoint=func.__module__,
                                     request_body=request_body,
                                     status=tasks.RequestStatus.PENDING)
    tasks.dump_reqest(request_task)
    request_task.log_path.touch()
    kwargs['env_vars'] = request_body.get('env_vars', {})
    kwargs['ignore_return_value'] = ignore_return_value
    process = multiprocessing.Process(target=_wrapper,
                                      args=(func, request_id, *args),
                                      kwargs=kwargs)
    process.start()
