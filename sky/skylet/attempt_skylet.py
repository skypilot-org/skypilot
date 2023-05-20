"""Restarts skylet if version does not match"""

import os
import subprocess

from sky.skylet import constants


def restart_skylet(kill_old=False):
    with open(constants.SKYLET_VERSION_FILE, 'w+') as v_f:
        v_f.write(constants.SKYLET_VERSION)

    if kill_old:
        #pylint: disable=subprocess-run-check
        subprocess.run(
            'ps aux | grep "sky.skylet.skylet" | grep "python3 -m"'
            '| awk \'{print $2}\' | xargs kill',
            shell=True,
        )
    subprocess.run(
        'nohup python3 -m sky.skylet.skylet'
        ' >> ~/.sky/skylet.log 2>&1 &',
        shell=True,
        check=True)


proc = subprocess.run(
    'ps aux | grep "sky.skylet.skylet" | grep "python3 -m"'
    '| grep -v "grep"',
    shell=True,
    capture_output=True,
    check=True)

running = proc.stdout.decode().strip().replace('\n', '')

if not running:
    restart_skylet(kill_old=False)

version_match = False
if os.path.exists(constants.SKYLET_VERSION_FILE):
    with open(constants.SKYLET_VERSION_FILE) as f:
        if f.read() == constants.SKYLET_VERSION:
            version_match = True

if not version_match:
    restart_skylet(kill_old=True)
