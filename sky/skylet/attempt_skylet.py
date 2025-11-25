"""Restarts skylet if version does not match"""

import os
import signal
import subprocess

from sky.skylet import constants
from sky.utils import common_utils

SKY_RUNTIME_DIR = os.environ.get(constants.SKY_RUNTIME_DIR_ENV_VAR,
                                 os.path.expanduser('~'))
VERSION_FILE = os.path.join(SKY_RUNTIME_DIR, '.sky/skylet_version')
SKYLET_LOG_FILE = os.path.join(SKY_RUNTIME_DIR, '.sky/skylet.log')
PID_FILE = os.path.join(SKY_RUNTIME_DIR, '.sky/skylet_pid')
PORT_FILE = os.path.join(SKY_RUNTIME_DIR, '.sky/skylet_port')


def restart_skylet():
    # Kills old skylet if it is running.
    # TODO(zhwu): make the killing graceful, e.g., use a signal to tell
    # skylet to exit, instead of directly killing it.

    # Kill only our skylet process (identified by stored PID)
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, 'r', encoding='utf-8') as pid_file:
                old_pid = int(pid_file.read().strip())
            # Check if process is still alive and kill it
            try:
                os.kill(old_pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                # Assume process already died
                pass
        except (OSError, ValueError, IOError) as exc:
            raise RuntimeError(f'Failed to read PID file {PID_FILE}: '
                               f'{common_utils.format_exception(exc)}') from exc
    else:
        # Fall back to old behavior for backward compatibility
        subprocess.run(
            # We use -m to grep instead of {constants.SKY_PYTHON_CMD} -m
            # to grep because need to handle the backward compatibility of
            # the old skylet started before #3326, which does not use the
            # full path to python.
            'ps aux | grep "sky.skylet.skylet" | grep " -m "'
            f'| awk \'{{print $2}}\' | xargs kill >> {SKYLET_LOG_FILE} 2>&1',
            shell=True,
            check=False)

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


# Check if our skylet is running by checking the PID file
running = False
if os.path.exists(PID_FILE):
    try:
        with open(PID_FILE, 'r', encoding='utf-8') as f:
            pid = int(f.read().strip())
        # Check if the process is still alive
        os.kill(pid, 0)
        running = True
    except Exception as e:  # pylint: disable=broad-except
        # Assume the process is not running and restart it
        pass
else:
    # Fall back to grep-based check for backward compatibility
    proc = subprocess.run(
        'ps aux | grep -v "grep" | grep "sky.skylet.skylet" | grep " -m"',
        shell=True,
        check=False)
    running = (proc.returncode == 0)

version_match = False
found_version = None
if os.path.exists(VERSION_FILE):
    with open(VERSION_FILE, 'r', encoding='utf-8') as f:
        found_version = f.read().strip()
        if found_version == constants.SKYLET_VERSION:
            version_match = True

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
