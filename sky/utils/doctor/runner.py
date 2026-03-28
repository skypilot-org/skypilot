"""Orchestration layer for ``sky doctor``.

Responsibilities:
- Build an SSH connection to the cluster head node from a cluster record.
- Auto-detect which accelerator plugin applies (or fall back to a CPU-only
  report).
- Run each check in order and collect results.
- Render a formatted table of results to the terminal.
"""
import sys
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

import colorama

from sky.utils.doctor.amd_gpu import AmdGpuDoctorPlugin
from sky.utils.doctor.base import AcceleratorDoctorPlugin
from sky.utils.doctor.base import CheckResult
from sky.utils.doctor.base import CheckStatus
from sky.utils.doctor.base import RemoteRunner
from sky.utils.doctor.nvidia_gpu import NvidiaGpuDoctorPlugin
from sky.utils.doctor.tpu import TpuDoctorPlugin

if TYPE_CHECKING:
    from sky.schemas.api import responses as response_types

# Ordered list of plugins tried during auto-detection.
_PLUGINS = [NvidiaGpuDoctorPlugin, AmdGpuDoctorPlugin, TpuDoctorPlugin]

# Terminal colour helpers
_RESET = colorama.Style.RESET_ALL
_BOLD = colorama.Style.BRIGHT
_DIM = colorama.Style.DIM
_GREEN = colorama.Fore.GREEN
_YELLOW = colorama.Fore.YELLOW
_RED = colorama.Fore.RED
_CYAN = colorama.Fore.CYAN
_WHITE = colorama.Fore.WHITE

_STATUS_STYLE: Dict[CheckStatus, str] = {
    CheckStatus.OK: _GREEN + _BOLD,
    CheckStatus.WARNING: _YELLOW + _BOLD,
    CheckStatus.FAIL: _RED + _BOLD,
    CheckStatus.SKIPPED: _DIM,
}

_STATUS_LABEL: Dict[CheckStatus, str] = {
    CheckStatus.OK: ' OK  ',
    CheckStatus.WARNING: 'WARN ',
    CheckStatus.FAIL: 'FAIL ',
    CheckStatus.SKIPPED: 'SKIP ',
}

# Column widths
_COL_STATUS = 6
_COL_NAME = 30
_COL_MESSAGE = 60


def _make_remote_runner(
        record: 'response_types.StatusResponse') -> RemoteRunner:
    """Build a RemoteRunner callable from a cluster status record.

    The record is expected to have been retrieved with ``_include_credentials``
    so that ``record['credentials']`` is populated.

    Returns a callable that runs a shell command on the cluster head node and
    returns (returncode, stdout, stderr).
    """
    # pylint: disable=import-outside-toplevel
    from sky.utils.command_runner import SSHCommandRunner
    handle = record['handle']
    credentials: Dict = record.get('credentials', {})

    ips: Optional[List[str]] = handle.cached_external_ips
    ports: Optional[List[int]] = handle.cached_external_ssh_ports

    if not ips:
        raise ValueError(
            f'Cluster {record["name"]!r} has no known external IPs. '
            'Is the cluster running?')

    head_ip = ips[0]
    head_port = ports[0] if ports else 22

    ssh_user = credentials.get('ssh_user') or handle.ssh_user or 'ubuntu'
    ssh_private_key = credentials.get('ssh_private_key')
    ssh_control_name = credentials.get('ssh_control_name')
    ssh_proxy_command = credentials.get('ssh_proxy_command')
    ssh_proxy_jump = credentials.get('ssh_proxy_jump')
    docker_user = handle.docker_user

    runner = SSHCommandRunner(
        node=(head_ip, head_port),
        ssh_user=ssh_user,
        ssh_private_key=ssh_private_key,
        ssh_control_name=ssh_control_name,
        ssh_proxy_command=ssh_proxy_command,
        ssh_proxy_jump=ssh_proxy_jump,
        docker_user=docker_user,
    )

    def run_cmd(cmd: str) -> Tuple[int, str, str]:
        # pylint: disable=import-outside-toplevel
        from sky.utils.command_runner import SshMode
        rc, stdout, stderr = runner.run(
            cmd,
            require_outputs=True,
            stream_logs=False,
            ssh_mode=SshMode.NON_INTERACTIVE,
        )
        return rc, stdout, stderr

    return run_cmd


def _detect_plugin(run_cmd: RemoteRunner) -> Optional[AcceleratorDoctorPlugin]:
    """Return the first plugin that detects its accelerator, or None."""
    for plugin_cls in _PLUGINS:
        if plugin_cls.detect(run_cmd):
            return plugin_cls(run_cmd)  # type: ignore[abstract]
    return None


def _print_header(cluster_name: str, plugin_name: str) -> None:
    width = max(len(cluster_name) + 20, 60)
    print()
    print(_BOLD + _CYAN + '=' * width + _RESET)
    print(_BOLD + _CYAN + f'  sky doctor — {cluster_name}  [{plugin_name}]' +
          _RESET)
    print(_BOLD + _CYAN + '=' * width + _RESET)
    print()


def _print_result(result: CheckResult, verbose: bool) -> None:
    style = _STATUS_STYLE[result.status]
    label = _STATUS_LABEL[result.status]
    name_col = result.name.ljust(_COL_NAME)
    msg_col = result.message
    print(f'  [{style}{label}{_RESET}]  {_BOLD}{name_col}{_RESET}  {msg_col}')
    if verbose and result.details:
        for line in result.details.splitlines():
            print(f'           {_DIM}{line}{_RESET}')


def _print_summary(results: List[CheckResult]) -> None:
    counts = {s: 0 for s in CheckStatus}
    for r in results:
        counts[r.status] += 1
    print()
    print(_BOLD + '─' * 60 + _RESET)
    ok = counts[CheckStatus.OK]
    warn = counts[CheckStatus.WARNING]
    fail = counts[CheckStatus.FAIL]
    skip = counts[CheckStatus.SKIPPED]
    parts = []
    if ok:
        parts.append(f'{_GREEN}{_BOLD}{ok} passed{_RESET}')
    if warn:
        parts.append(f'{_YELLOW}{_BOLD}{warn} warning(s){_RESET}')
    if fail:
        parts.append(f'{_RED}{_BOLD}{fail} failed{_RESET}')
    if skip:
        parts.append(f'{_DIM}{skip} skipped{_RESET}')
    print('  Summary: ' + ', '.join(parts))
    print()


def run_doctor(record: 'response_types.StatusResponse',
               verbose: bool = False) -> bool:
    """Run all diagnostics for the given cluster record.

    Args:
        record: Cluster status record (must include credentials).
        verbose: If True, print detailed output for each check.

    Returns:
        True if all checks passed or were only warnings, False if any FAIL.
    """
    cluster_name = record['name']

    try:
        run_cmd = _make_remote_runner(record)
    except ValueError as e:
        print(f'{_RED}Error:{_RESET} {e}', file=sys.stderr)
        return False

    # Auto-detect plugin.
    plugin = _detect_plugin(run_cmd)
    if plugin is None:
        print(f'{_YELLOW}No recognised accelerator found on cluster '
              f'{cluster_name!r}.{_RESET}')
        print('Supported: NVIDIA GPU (nvidia-smi), AMD GPU (rocm-smi), '
              'Google TPU (/dev/accel0 or tpu-info).')
        return True

    _print_header(cluster_name, plugin.NAME)

    results: List[CheckResult] = []
    for check_fn in plugin.checks():
        try:
            result = check_fn()
        except Exception as exc:  # pylint: disable=broad-except
            result = CheckResult.fail(
                getattr(check_fn, '__name__', 'unknown'),
                f'Check raised an unexpected exception: {exc}')
        results.append(result)
        _print_result(result, verbose)

    _print_summary(results)

    any_fail = any(r.status == CheckStatus.FAIL for r in results)
    return not any_fail
