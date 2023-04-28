"""Restarts skylet if version does not match"""

import os
import subprocess
import sys

from sky.skylet import constants

if os.path.exists(constants.SKYLET_VERSION_FILE):
    with open(constants.SKYLET_VERSION_FILE) as f:
        if f.read() == constants.SKYLET_VERSION:
            sys.exit(0)

with open(constants.SKYLET_VERSION_FILE, 'w+') as f:
    f.write(constants.SKYLET_VERSION)

subprocess.run(
    'pkill -f "python3 -m sky.skylet.skylet"',
    shell = True, check=True)
subprocess.run(
    'nohup python3 -m sky.skylet.skylet >> ~/.sky/skylet.log 2>&1 &',
    shell=True, check=True)
