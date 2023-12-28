"""Restarts skylet if version does not match"""

import os
import subprocess

from sky.skylet import constants

VERSION_FILE = os.path.expanduser(constants.SKYLET_VERSION_FILE)


def restart_skylet():
    # Kills old skylet if it is running.
    # TODO(zhwu): make the killing graceful, e.g., use a signal to tell
    # skylet to exit, instead of directly killing it.
    subprocess.run(
        'ps aux | grep "sky.skylet.skylet" | grep "python3 -m"'
        '| awk \'{print $2}\' | xargs kill >> ~/.sky/skylet.log 2>&1',
        shell=True,
        check=False)
    subprocess.run(
        'nohup python3 -m sky.skylet.skylet'
        ' >> ~/.sky/skylet.log 2>&1 &',
        shell=True,
        check=True)
    with open(VERSION_FILE, 'w') as v_f:
        v_f.write(constants.SKYLET_VERSION)


proc = subprocess.run(
    'ps aux | grep -v "grep" | grep "sky.skylet.skylet" | grep "python3 -m"',
    shell=True,
    check=False)

running = (proc.returncode == 0)

version_match = False
found_version = None
if os.path.exists(VERSION_FILE):
    with open(VERSION_FILE) as f:
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
