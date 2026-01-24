"""Cross-platform compatibility utilities for SkyPilot.

This module provides abstractions for platform-specific functionality,
allowing SkyPilot to work on both Unix-like systems (Linux, macOS) and Windows.
"""
import os
import platform
import signal
import subprocess
import sys
from typing import Callable, List, Optional

# Platform detection constants
IS_WINDOWS = sys.platform == 'win32'
IS_MACOS = sys.platform == 'darwin'
IS_LINUX = sys.platform.startswith('linux')

# Signal availability - Windows only supports a subset of signals
if IS_WINDOWS:
    # Windows doesn't have these signals
    SIGTERM = signal.SIGTERM
    SIGKILL = signal.SIGTERM  # Windows doesn't have SIGKILL, use SIGTERM
    SIGHUP: Optional[signal.Signals] = None
    SIGUSR1: Optional[signal.Signals] = None
    SIGUSR2: Optional[signal.Signals] = None
else:
    SIGTERM = signal.SIGTERM
    SIGKILL = signal.SIGKILL
    SIGHUP = signal.SIGHUP
    SIGUSR1 = signal.SIGUSR1
    SIGUSR2 = signal.SIGUSR2


def get_null_device() -> str:
    """Returns the platform-appropriate null device path.

    Returns:
        '/dev/null' on Unix, 'NUL' on Windows (via os.devnull).
    """
    return os.devnull


def get_shell_executable() -> Optional[str]:
    """Returns the path to the shell executable for subprocess calls.

    Returns:
        Path to bash on Unix, None on Windows (uses default cmd.exe).
    """
    if IS_WINDOWS:
        return None
    return '/bin/bash'


def get_temp_dir() -> str:
    """Returns the platform-appropriate temp directory.

    Returns:
        Temp directory path appropriate for the platform.
    """
    import tempfile
    return tempfile.gettempdir()


def signal_handler_supported(sig: Optional[signal.Signals]) -> bool:
    """Check if a signal is supported on the current platform.

    Args:
        sig: The signal to check.

    Returns:
        True if the signal is supported, False otherwise.
    """
    if sig is None:
        return False
    if IS_WINDOWS:
        # Windows only supports SIGTERM, SIGABRT, SIGINT
        return sig in (signal.SIGTERM, signal.SIGABRT, signal.SIGINT)
    return True


def set_signal_handler(sig: Optional[signal.Signals],
                       handler: Callable) -> Optional[Callable]:
    """Set a signal handler if the signal is supported.

    Args:
        sig: The signal to handle.
        handler: The handler function.

    Returns:
        The previous handler if successful, None if signal not supported.
    """
    if not signal_handler_supported(sig):
        return None
    return signal.signal(sig, handler)  # type: ignore


def kill_process(pid: int, sig: Optional[signal.Signals] = None) -> bool:
    """Kill a process with the given signal.

    Args:
        pid: Process ID to kill.
        sig: Signal to send (defaults to SIGTERM).

    Returns:
        True if successful, False otherwise.
    """
    if sig is None:
        sig = SIGTERM
    try:
        if IS_WINDOWS:
            # On Windows, use os.kill which works differently
            os.kill(pid, sig)  # type: ignore
        else:
            os.kill(pid, sig)
        return True
    except (OSError, ProcessLookupError):
        return False


def kill_process_group(pgid: int, sig: Optional[signal.Signals] = None) -> bool:
    """Kill a process group with the given signal.

    Args:
        pgid: Process group ID to kill.
        sig: Signal to send (defaults to SIGTERM).

    Returns:
        True if successful, False otherwise.
    """
    if sig is None:
        sig = SIGTERM
    if IS_WINDOWS:
        # Windows doesn't have process groups in the same way
        # Fall back to killing the individual process
        return kill_process(pgid, sig)
    try:
        os.killpg(pgid, sig)
        return True
    except (OSError, ProcessLookupError):
        return False


def get_process_group_id(pid: int) -> Optional[int]:
    """Get the process group ID for a process.

    Args:
        pid: Process ID.

    Returns:
        Process group ID, or None if not available (e.g., on Windows).
    """
    if IS_WINDOWS:
        return None
    try:
        return os.getpgid(pid)
    except (OSError, ProcessLookupError):
        return None


def create_new_session() -> None:
    """Create a new session (setsid on Unix, no-op on Windows)."""
    if not IS_WINDOWS:
        os.setsid()


def fork_process() -> int:
    """Fork the process (Unix only).

    Returns:
        PID of child process in parent, 0 in child, -1 on Windows.
    """
    if IS_WINDOWS:
        return -1
    return os.fork()


def daemonize() -> bool:
    """Daemonize the current process.

    On Unix, uses double-fork technique.
    On Windows, this is not directly possible - returns False.

    Returns:
        True if successfully daemonized (in the daemon process),
        False on Windows or on error.
    """
    if IS_WINDOWS:
        # Windows doesn't support fork-based daemonization
        # The calling code should use alternative approaches
        # (e.g., pythonw.exe, Windows services, etc.)
        return False

    # First fork
    try:
        pid = os.fork()
        if pid > 0:
            # Parent exits
            sys.exit(0)
    except OSError:
        return False

    # Decouple from parent
    os.setsid()

    # Second fork
    try:
        pid = os.fork()
        if pid > 0:
            # First child exits
            sys.exit(0)
    except OSError:
        return False

    return True


def start_detached_process(cmd: List[str],
                           env: Optional[dict] = None) -> subprocess.Popen:
    """Start a detached process that survives parent termination.

    Args:
        cmd: Command and arguments as a list.
        env: Optional environment variables.

    Returns:
        Popen object for the started process.
    """
    kwargs = {
        'stdout': subprocess.DEVNULL,
        'stderr': subprocess.DEVNULL,
        'stdin': subprocess.DEVNULL,
    }

    if env is not None:
        kwargs['env'] = env

    if IS_WINDOWS:
        # Windows: Use CREATE_NEW_PROCESS_GROUP and DETACHED_PROCESS flags
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        DETACHED_PROCESS = 0x00000008
        kwargs['creationflags'] = CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
    else:
        # Unix: Start new session
        kwargs['start_new_session'] = True

    return subprocess.Popen(cmd, **kwargs)


def get_resource_limit(resource_type: str) -> Optional[int]:
    """Get a resource limit value.

    Args:
        resource_type: Type of resource ('nofile' for file descriptors).

    Returns:
        The soft limit value, or None if not available (e.g., on Windows).
    """
    if IS_WINDOWS:
        # Windows doesn't have POSIX resource limits
        # Return a reasonable default for file descriptors
        if resource_type == 'nofile':
            return 8192  # Default Windows handle limit
        return None

    import resource as resource_module
    if resource_type == 'nofile':
        soft, _ = resource_module.getrlimit(resource_module.RLIMIT_NOFILE)
        return soft
    return None


def is_wsl() -> bool:
    """Detect if running under Windows Subsystem for Linux (WSL)."""
    if IS_WINDOWS:
        return False
    return 'microsoft' in platform.uname().release.lower()


def get_terminal_size() -> tuple:
    """Get terminal size in a cross-platform way.

    Returns:
        Tuple of (columns, lines).
    """
    try:
        return os.get_terminal_size()
    except OSError:
        return (80, 24)  # Default fallback


def supports_pty() -> bool:
    """Check if the platform supports PTY (pseudo-terminal).

    Returns:
        True on Unix, False on Windows.
    """
    return not IS_WINDOWS


def supports_fcntl() -> bool:
    """Check if the platform supports fcntl.

    Returns:
        True on Unix, False on Windows.
    """
    return not IS_WINDOWS


def path_to_posix(path: str) -> str:
    """Convert a path to POSIX format (forward slashes).

    This is useful for paths that will be used in shell commands
    or on remote Unix systems.

    Args:
        path: The path to convert.

    Returns:
        Path with forward slashes.
    """
    if IS_WINDOWS:
        return path.replace('\\', '/')
    return path


def normalize_path(path: str) -> str:
    """Normalize a path for the current platform.

    Args:
        path: The path to normalize.

    Returns:
        Normalized path.
    """
    return os.path.normpath(os.path.expanduser(path))


class UnsupportedPlatformError(Exception):
    """Exception raised when an operation is not supported on the platform."""

    def __init__(self, operation: str):
        self.operation = operation
        super().__init__(
            f"Operation '{operation}' is not supported on {sys.platform}")
