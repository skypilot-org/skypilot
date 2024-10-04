import enum
from typing import Any, Iterable, List, Optional, Tuple, Union

from _typeshed import Incomplete

from sky import sky_logging as sky_logging
from sky.skylet import constants as constants
from sky.skylet import log_lib as log_lib
from sky.utils import common_utils as common_utils
from sky.utils import subprocess_utils as subprocess_utils
from sky.utils import timeline as timeline

logger: Incomplete
GIT_EXCLUDE: str
RSYNC_DISPLAY_OPTION: str
RSYNC_FILTER_GITIGNORE: Incomplete
RSYNC_FILTER_SKYIGNORE: Incomplete
RSYNC_EXCLUDE_OPTION: str
ALIAS_SUDO_TO_EMPTY_FOR_ROOT_CMD: str


def ssh_options_list(ssh_private_key: Optional[str],
                     ssh_control_name: Optional[str],
                     *,
                     ssh_proxy_command: Optional[str] = ...,
                     docker_ssh_proxy_command: Optional[str] = ...,
                     connect_timeout: Optional[int] = ...,
                     port: int = ...,
                     disable_control_master: Optional[bool] = ...) -> List[str]:
    ...


class SshMode(enum.Enum):
    NON_INTERACTIVE: int
    INTERACTIVE: int
    LOGIN: int


class CommandRunner:
    node: Incomplete

    def __init__(self, node: Tuple[Any, Any], **kwargs) -> None:
        ...

    @property
    def node_id(self) -> str:
        ...

    def run(self,
            cmd: Union[str, List[str]],
            *,
            require_outputs: bool = ...,
            log_path: str = ...,
            process_stream: bool = ...,
            stream_logs: bool = ...,
            ssh_mode: SshMode = ...,
            separate_stderr: bool = ...,
            connect_timeout: Optional[int] = ...,
            source_bashrc: bool = ...,
            skip_lines: int = ...,
            **kwargs) -> Union[int, Tuple[int, str, str]]:
        ...

    def rsync(self,
              source: str,
              target: str,
              *,
              up: bool,
              log_path: str = ...,
              stream_logs: bool = ...,
              max_retry: int = ...) -> None:
        ...

    @classmethod
    def make_runner_list(cls, node_list: Iterable[Any],
                         **kwargs) -> List['CommandRunner']:
        ...

    def check_connection(self) -> bool:
        ...

    def close_cached_connection(self) -> None:
        ...


class SSHCommandRunner(CommandRunner):
    ssh_private_key: Incomplete
    ssh_control_name: Incomplete
    disable_control_master: Incomplete
    ip: str
    ssh_user: Incomplete
    port: Incomplete

    def __init__(self,
                 node: Tuple[str, int],
                 ssh_user: str,
                 ssh_private_key: str,
                 ssh_control_name: Optional[str] = ...,
                 ssh_proxy_command: Optional[str] = ...,
                 docker_user: Optional[str] = ...,
                 disable_control_master: Optional[bool] = ...) -> None:
        ...

    def close_cached_connection(self) -> None:
        ...

    def run(self,
            cmd: Union[str, List[str]],
            *,
            require_outputs: bool = ...,
            port_forward: Optional[List[int]] = ...,
            log_path: str = ...,
            process_stream: bool = ...,
            stream_logs: bool = ...,
            ssh_mode: SshMode = ...,
            separate_stderr: bool = ...,
            connect_timeout: Optional[int] = ...,
            source_bashrc: bool = ...,
            skip_lines: int = ...,
            **kwargs) -> Union[int, Tuple[int, str, str]]:
        ...

    def rsync(self,
              source: str,
              target: str,
              *,
              up: bool,
              log_path: str = ...,
              stream_logs: bool = ...,
              max_retry: int = ...) -> None:
        ...


class KubernetesCommandRunner(CommandRunner):

    def __init__(self, node: Tuple[Tuple[str, Optional[str]], str],
                 **kwargs) -> None:
        ...

    @property
    def node_id(self) -> str:
        ...

    def run(self,
            cmd: Union[str, List[str]],
            *,
            port_forward: Optional[List[int]] = ...,
            require_outputs: bool = ...,
            log_path: str = ...,
            process_stream: bool = ...,
            stream_logs: bool = ...,
            ssh_mode: SshMode = ...,
            separate_stderr: bool = ...,
            connect_timeout: Optional[int] = ...,
            source_bashrc: bool = ...,
            skip_lines: int = ...,
            **kwargs) -> Union[int, Tuple[int, str, str]]:
        ...

    def rsync(self,
              source: str,
              target: str,
              *,
              up: bool,
              log_path: str = ...,
              stream_logs: bool = ...,
              max_retry: int = ...) -> None:
        ...
