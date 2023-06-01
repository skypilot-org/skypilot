"""Restarts skylet if version does not match"""

import os
import subprocess

from sky.skylet import constants

VERSION_FILE = os.path.expanduser(constants.SKYLET_VERSION_FILE)

def restart_skylet():
    with open(VERSION_FILE, 'w+') as v_f:
        v_f.write(constants.SKYLET_VERSION)

    # Kills old skylet if it is running
    subprocess.run(
        'ps aux | grep "sky.skylet.skylet" | grep "python3 -m"'
        '| awk \'{print $2}\' | xargs kill',
        shell=True,
        check=False
    )
    subprocess.run(
        'nohup python3 -m sky.skylet.skylet'
        ' >> ~/.sky/skylet.log 2>&1 &',
        shell=True,
        check=True)
    
proc = subprocess.run(
    'ps aux | grep "sky.skylet.skylet" | grep "python3 -m"'
    '| grep -v "grep" || true',
    shell=True,
    capture_output=True,
    check=True)

running = proc.stdout.decode().strip().replace('\n', '')

version_match = False
if os.path.exists(VERSION_FILE):
    with open(VERSION_FILE) as f:
        if f.read() == constants.SKYLET_VERSION:
            version_match = True

if not running or not version_match:
    restart_skylet()
