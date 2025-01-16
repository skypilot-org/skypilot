"""Type stubs for log_lib.py.
This file is dynamically generated by stubgen and added with the
overloaded type hints for run_with_log(), as we need to determine
the return type based on the value of require_outputs.
"""
import typing
from typing import Dict, List, Optional, Tuple, Union

from typing_extensions import Literal

from sky import sky_logging as sky_logging
from sky.skylet import constants as constants
from sky.skylet import job_lib as job_lib
from sky.utils import log_utils as log_utils

LOG_FILE_START_STREAMING_AT: str = ...


class _ProcessingArgs:
    log_path: str
    stream_logs: bool
    start_streaming_at: str = ...
    end_streaming_at: Optional[str] = ...
    skip_lines: Optional[List[str]] = ...
    replace_crlf: bool = ...
    line_processor: Optional[log_utils.LineProcessor] = ...
    streaming_prefix: Optional[str] = ...

    def __init__(self,
                 log_path: str,
                 stream_logs: bool,
                 start_streaming_at: str = ...,
                 end_streaming_at: Optional[str] = ...,
                 skip_lines: Optional[List[str]] = ...,
                 replace_crlf: bool = ...,
                 line_processor: Optional[log_utils.LineProcessor] = ...,
                 streaming_prefix: Optional[str] = ...) -> None:
        ...


def _handle_io_stream(io_stream, out_stream, args: _ProcessingArgs):
    ...


def process_subprocess_stream(proc, args: _ProcessingArgs) -> Tuple[str, str]:
    ...


@typing.overload
def run_with_log(cmd: Union[List[str], str],
                 log_path: str,
                 *,
                 require_outputs: Literal[False] = False,
                 stream_logs: bool = ...,
                 start_streaming_at: str = ...,
                 end_streaming_at: Optional[str] = ...,
                 skip_lines: Optional[List[str]] = ...,
                 shell: bool = ...,
                 with_ray: bool = ...,
                 process_stream: bool = ...,
                 line_processor: Optional[log_utils.LineProcessor] = ...,
                 streaming_prefix: Optional[str] = ...,
                 ray_job_id: Optional[str] = ...,
                 **kwargs) -> int:
    ...


@typing.overload
def run_with_log(cmd: Union[List[str], str],
                 log_path: str,
                 *,
                 require_outputs: Literal[True],
                 stream_logs: bool = ...,
                 start_streaming_at: str = ...,
                 end_streaming_at: Optional[str] = ...,
                 skip_lines: Optional[List[str]] = ...,
                 shell: bool = ...,
                 with_ray: bool = ...,
                 process_stream: bool = ...,
                 line_processor: Optional[log_utils.LineProcessor] = ...,
                 streaming_prefix: Optional[str] = ...,
                 ray_job_id: Optional[str] = ...,
                 **kwargs) -> Tuple[int, str, str]:
    ...


@typing.overload
def run_with_log(cmd: Union[List[str], str],
                 log_path: str,
                 *,
                 require_outputs: bool = False,
                 stream_logs: bool = ...,
                 start_streaming_at: str = ...,
                 end_streaming_at: Optional[str] = ...,
                 skip_lines: Optional[List[str]] = ...,
                 shell: bool = ...,
                 with_ray: bool = ...,
                 process_stream: bool = ...,
                 line_processor: Optional[log_utils.LineProcessor] = ...,
                 streaming_prefix: Optional[str] = ...,
                 ray_job_id: Optional[str] = ...,
                 **kwargs) -> Union[int, Tuple[int, str, str]]:
    ...


def make_task_bash_script(codegen: str,
                          env_vars: Optional[Dict[str, str]] = ...) -> str:
    ...


def add_ray_env_vars(
        env_vars: Optional[Dict[str, str]] = ...) -> Dict[str, str]:
    ...


def run_bash_command_with_log(bash_command: str,
                              log_path: str,
                              env_vars: Optional[Dict[str, str]] = ...,
                              stream_logs: bool = ...,
                              with_ray: bool = ...):
    ...


def tail_logs(job_id: int,
              log_dir: Optional[str],
              managed_job_id: Optional[int] = ...,
              follow: bool = ...) -> None:
    ...
