"""Platform compatibility utilities for cross-platform support.

This module provides utilities for making SkyPilot compatible with different
operating systems, including Windows, macOS, and Linux.
"""
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from typing import List, Optional, Tuple

# Platform detection constants
IS_WINDOWS = sys.platform == 'win32'
IS_MACOS = sys.platform == 'darwin'
IS_LINUX = sys.platform.startswith('linux')


def is_windows() -> bool:
    """Check if running on Windows."""
    return IS_WINDOWS


def is_macos() -> bool:
    """Check if running on macOS."""
    return IS_MACOS


def is_linux() -> bool:
    """Check if running on Linux."""
    return IS_LINUX


def is_wsl() -> bool:
    """Detect if running under Windows Subsystem for Linux (WSL)."""
    if not IS_LINUX:
        return False
    return 'microsoft' in platform.uname().release.lower()


def get_null_device() -> str:
    """Get the null device path for the current platform.

    Returns:
        '/dev/null' on Unix-like systems, 'NUL' on Windows.
    """
    return os.devnull


def get_temp_dir() -> str:
    """Get the system temporary directory.

    Returns:
        The path to the system temporary directory.
    """
    return tempfile.gettempdir()


def get_shell() -> str:
    """Get the default shell for the current platform.

    Returns:
        Path to the default shell executable.
    """
    if IS_WINDOWS:
        # Check for bash (Git Bash, WSL, etc.) first
        bash_path = shutil.which('bash')
        if bash_path:
            return bash_path
        # Fall back to PowerShell or cmd
        pwsh = shutil.which('pwsh') or shutil.which('powershell')
        if pwsh:
            return pwsh
        return os.environ.get('COMSPEC', 'cmd.exe')
    return '/bin/bash'


def get_shell_and_args() -> Tuple[str, List[str]]:
    """Get the shell executable and arguments for running commands.

    Returns:
        A tuple of (shell_path, shell_args) suitable for subprocess execution.
    """
    if IS_WINDOWS:
        # Check for bash first
        bash_path = shutil.which('bash')
        if bash_path:
            return bash_path, ['--login', '-c']
        # PowerShell
        pwsh = shutil.which('pwsh') or shutil.which('powershell')
        if pwsh:
            return pwsh, ['-NoProfile', '-Command']
        # Fall back to cmd.exe
        return os.environ.get('COMSPEC', 'cmd.exe'), ['/c']
    return '/bin/bash', ['--login', '-c']


def has_bash() -> bool:
    """Check if bash is available on the system.

    Returns:
        True if bash is available, False otherwise.
    """
    return shutil.which('bash') is not None


def run_shell_command(cmd: str,
                      shell: bool = True,
                      **kwargs) -> subprocess.CompletedProcess:
    """Run a shell command in a cross-platform way.

    Args:
        cmd: The command to run.
        shell: Whether to run through a shell.
        **kwargs: Additional arguments to pass to subprocess.run.

    Returns:
        The CompletedProcess instance.
    """
    if shell:
        if IS_WINDOWS and not has_bash():
            # On Windows without bash, we need to handle command differently
            executable = kwargs.pop('executable', None)
            if executable is None:
                pwsh = shutil.which('pwsh') or shutil.which('powershell')
                if pwsh:
                    executable = pwsh
        else:
            executable = kwargs.pop('executable', '/bin/bash')
        return subprocess.run(cmd, shell=shell, executable=executable, **kwargs)
    return subprocess.run(cmd, shell=shell, **kwargs)


def get_home_dir() -> str:
    """Get the user's home directory in a cross-platform way.

    Returns:
        The path to the user's home directory.
    """
    return os.path.expanduser('~')


def make_temp_path(prefix: str = 'skypilot_', suffix: str = '') -> str:
    """Create a temporary path that works on all platforms.

    Args:
        prefix: Prefix for the temporary file/directory name.
        suffix: Suffix for the temporary file/directory name.

    Returns:
        A path in the system's temporary directory.
    """
    return os.path.join(get_temp_dir(), f'{prefix}{os.getpid()}{suffix}')


def make_skypilot_temp_dir(subdir: str = '') -> str:
    """Create a SkyPilot-specific temporary directory path.

    Args:
        subdir: Optional subdirectory name.

    Returns:
        A path suitable for SkyPilot temporary files.
    """
    # Import here to avoid circular dependency
    from sky.utils import common_utils
    user_hash = common_utils.get_user_hash()
    base = os.path.join(get_temp_dir(), f'skypilot_{user_hash}')
    if subdir:
        return os.path.join(base, subdir)
    return base


# Signal compatibility
def get_sigterm():
    """Get the SIGTERM signal number.

    On Windows, returns None as SIGTERM is not fully supported.
    """
    import signal
    if IS_WINDOWS:
        # Windows doesn't have SIGTERM, but signal module provides it
        return getattr(signal, 'SIGTERM', None)
    return signal.SIGTERM


def get_sigkill():
    """Get the SIGKILL signal number.

    On Windows, returns None as SIGKILL is not available.
    """
    import signal
    if IS_WINDOWS:
        return getattr(signal, 'SIGKILL', None)
    return signal.SIGKILL


def get_sighup():
    """Get the SIGHUP signal number.

    On Windows, returns None as SIGHUP is not available.
    """
    import signal
    if IS_WINDOWS:
        return getattr(signal, 'SIGHUP', None)
    return signal.SIGHUP


def supports_pty() -> bool:
    """Check if the platform supports PTY (pseudo-terminal).

    Returns:
        True if PTY is supported, False otherwise.
    """
    if IS_WINDOWS:
        return False
    try:
        import pty  # pylint: disable=import-outside-toplevel,unused-import
        return True
    except ImportError:
        return False


def supports_fork() -> bool:
    """Check if the platform supports os.fork().

    Returns:
        True if fork is supported, False otherwise.
    """
    return hasattr(os, 'fork') and not IS_WINDOWS


def supports_unix_sockets() -> bool:
    """Check if the platform supports Unix domain sockets.

    Returns:
        True if Unix sockets are supported, False otherwise.
    """
    import socket
    return hasattr(socket, 'AF_UNIX') and not IS_WINDOWS


def supports_signals() -> bool:
    """Check if the platform supports Unix-style signal handling.

    Returns:
        True if full signal handling is supported, False otherwise.
    """
    return not IS_WINDOWS


def supports_process_groups() -> bool:
    """Check if the platform supports process groups (os.setpgid, os.killpg).

    Returns:
        True if process groups are supported, False otherwise.
    """
    return hasattr(os, 'setpgid') and hasattr(os, 'killpg') and not IS_WINDOWS


def terminate_process(proc: subprocess.Popen,
                      force: bool = False,
                      timeout: float = 5.0) -> None:
    """Terminate a process in a cross-platform way.

    Args:
        proc: The subprocess.Popen instance to terminate.
        force: If True, use SIGKILL/kill instead of SIGTERM/terminate.
        timeout: Time to wait for process to terminate before force killing.
    """
    if proc.poll() is not None:
        # Process already terminated
        return

    try:
        if force:
            proc.kill()
        else:
            proc.terminate()
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Force kill if graceful termination timed out
        proc.kill()
        proc.wait(timeout=timeout)
    except (ProcessLookupError, OSError):
        # Process already terminated
        pass


def set_file_executable(path: str) -> None:
    """Set file as executable in a cross-platform way.

    Args:
        path: Path to the file.
    """
    if IS_WINDOWS:
        # Windows doesn't have executable bits, but the file should
        # still work if it has a proper shebang or is a .bat/.cmd file
        pass
    else:
        os.chmod(path, 0o755)


def create_symlink(source: str, link_name: str) -> None:
    """Create a symbolic link in a cross-platform way.

    Args:
        source: The target of the symbolic link.
        link_name: The name of the symbolic link to create.

    Raises:
        OSError: If symlink creation fails (e.g., on Windows without
            developer mode or admin privileges).
    """
    os.symlink(source, link_name)


def path_to_posix(path: str) -> str:
    """Convert a path to POSIX format (forward slashes).

    This is useful when generating paths that will be used on remote
    Unix systems or in shell commands.

    Args:
        path: The path to convert.

    Returns:
        The path with forward slashes.
    """
    return path.replace(os.sep, '/')


def path_to_native(path: str) -> str:
    """Convert a POSIX path to the native format.

    Args:
        path: The path to convert.

    Returns:
        The path with native separators.
    """
    return path.replace('/', os.sep)


def get_resource_limit(resource_type: str) -> Optional[Tuple[int, int]]:
    """Get resource limits in a cross-platform way.

    Args:
        resource_type: The type of resource ('nofile', 'nproc', etc.)

    Returns:
        A tuple of (soft_limit, hard_limit) or None if not supported.
    """
    if IS_WINDOWS:
        # Windows doesn't have the same resource limit concept
        # Return reasonable defaults
        if resource_type == 'nofile':
            # Windows has a much higher file handle limit
            return (8192, 8192)
        return None

    try:
        import resource  # pylint: disable=import-outside-toplevel
        resource_map = {
            'nofile': resource.RLIMIT_NOFILE,
            'nproc': resource.RLIMIT_NPROC,
        }
        if resource_type in resource_map:
            return resource.getrlimit(resource_map[resource_type])
        return None
    except (ImportError, AttributeError):
        return None


def redirect_to_null() -> str:
    """Get the shell redirection string to null device.

    Returns:
        The appropriate redirection string for the current platform.
    """
    if IS_WINDOWS and not has_bash():
        return '> NUL 2>&1'
    return f'> {os.devnull} 2>&1'


def get_ssh_options_for_platform() -> List[str]:
    """Get SSH options that work on the current platform.

    Returns:
        A list of SSH options.
    """
    options = []
    if IS_WINDOWS:
        # Windows OpenSSH might need different options
        pass
    return options


def check_command_exists(cmd: str) -> bool:
    """Check if a command exists on the system.

    Args:
        cmd: The command to check.

    Returns:
        True if the command exists, False otherwise.
    """
    return shutil.which(cmd) is not None


def get_cpu_affinity() -> Optional[int]:
    """Get the CPU affinity count if available.

    Returns:
        The number of CPUs in the affinity set, or None if not available.
    """
    if IS_WINDOWS:
        return None
    if hasattr(os, 'sched_getaffinity'):
        return len(os.sched_getaffinity(0))
    return None
