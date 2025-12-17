"""Restarts skylet if version does not match"""

import os
import signal
import subprocess
from typing import List, Optional, Tuple

import psutil

from sky.skylet import constants
from sky.skylet import runtime_utils
from sky.utils import common_utils

VERSION_FILE = runtime_utils.get_runtime_dir_path(constants.SKYLET_VERSION_FILE)
SKYLET_LOG_FILE = runtime_utils.get_runtime_dir_path(constants.SKYLET_LOG_FILE)
PID_FILE = runtime_utils.get_runtime_dir_path(constants.SKYLET_PID_FILE)
PORT_FILE = runtime_utils.get_runtime_dir_path(constants.SKYLET_PORT_FILE)


def _is_running_skylet_process(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        process = psutil.Process(pid)
        if not process.is_running():
            return False
        # Check if command line contains the skylet module identifier
        cmdline = process.cmdline()
        return any('sky.skylet.skylet' in arg for arg in cmdline)
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess,
            OSError) as e:
        print(f'Error checking if skylet process {pid} is running: {e}')
        return False


def _find_running_skylet_pids() -> List[int]:
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, 'r', encoding='utf-8') as pid_file:
                pid = int(pid_file.read().strip())
            if _is_running_skylet_process(pid):
                return [pid]
        except (OSError, ValueError, IOError) as e:
            # Don't fallback to grep-based detection as the existence of the
            # PID file implies that we are on the new version, and there is
            # possibility of there being multiple skylet processes running,
            # and we don't want to accidentally kill the wrong skylet(s).
            print(f'Error reading PID file {PID_FILE}: {e}')
        return []
    else:
        # Fall back to grep-based detection for backward compatibility.
        pids = []
        # We use -m to grep instead of {constants.SKY_PYTHON_CMD} -m to grep
        # because need to handle the backward compatibility of the old skylet
        # started before #3326, which does not use the full path to python.
        proc = subprocess.run(
            'ps aux | grep -v "grep" | grep "sky.skylet.skylet" | grep " -m"',
            shell=True,
            check=False,
            capture_output=True,
            text=True)
        if proc.returncode == 0:
            # Parse the output to extract PIDs (column 2)
            for line in proc.stdout.strip().split('\n'):
                if line:
                    parts = line.split()
                    if len(parts) >= 2:
                        try:
                            pids.append(int(parts[1]))
                        except ValueError:
                            continue
        return pids


def _check_version_match() -> Tuple[bool, Optional[str]]:
    """Check if the version file matches the current skylet version.

    Returns:
        Tuple of (version_match: bool, version: str or None)
    """
    version: Optional[str] = None
    if os.path.exists(VERSION_FILE):
        try:
            with open(VERSION_FILE, 'r', encoding='utf-8') as f:
                version = f.read().strip()
                return version == constants.SKYLET_VERSION, version
        except (OSError, IOError):
            pass
    return False, version


def restart_skylet():
    # Kills old skylet if it is running.
    # TODO(zhwu): make the killing graceful, e.g., use a signal to tell
    # skylet to exit, instead of directly killing it.

    # Find and kill running skylet processes
    for pid in _find_running_skylet_pids():
        try:
            os.kill(pid, signal.SIGKILL)
            # Wait until process fully terminates so its socket gets released.
            # Without this, find_free_port may race with the kernel closing the
            # socket and fail to bind to the port that's supposed to be free.
            psutil.Process(pid).wait(timeout=5)
        except (OSError, ProcessLookupError, psutil.NoSuchProcess,
                psutil.TimeoutExpired):
            # Process died between detection and kill, or timeout waiting
            pass
    # Clean up the PID file
    try:
        os.remove(PID_FILE)
    except OSError:
        pass  # Best effort cleanup

    # TODO(kevin): Handle race conditions here. Race conditions can only
    # happen on Slurm, where there could be multiple clusters running in
    # one network namespace. For other clouds, the behaviour will be that
    # it always gets port 46590 (default port).
    port = common_utils.find_free_port(constants.SKYLET_GRPC_PORT)
    subprocess.run(
        # We have made sure that `attempt_skylet.py` is executed with the
        # skypilot runtime env activated, so that skylet can access the cloud
        # CLI tools.
        f'nohup {constants.SKY_PYTHON_CMD} -m sky.skylet.skylet '
        f'--port={port} '
        f'>> {SKYLET_LOG_FILE} 2>&1 & echo $! > {PID_FILE}',
        shell=True,
        check=True)

    with open(PORT_FILE, 'w', encoding='utf-8') as pf:
        pf.write(str(port))

    with open(VERSION_FILE, 'w', encoding='utf-8') as v_f:
        v_f.write(constants.SKYLET_VERSION)


# Check if our skylet is running
running = bool(_find_running_skylet_pids())

version_match, found_version = _check_version_match()

version_string = (f' (found version {found_version}, new version '
                  f'{constants.SKYLET_VERSION})')
if not running:
    print('Skylet is not running. Starting (version '
          f'{constants.SKYLET_VERSION})...')
elif not version_match:
    print(f'Skylet is stale{version_string}. Restarting...')
else:
    print(
        f'Skylet is running with the latest version {constants.SKYLET_VERSION}.'
    )

if not running or not version_match:
    restart_skylet()
