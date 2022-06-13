"""Utils shared between all of sky"""

import sys
import time
import os
import uuid

transaction_id = time.time()


def get_pretty_entry_point() -> str:
    """Returns the prettified entry point of this process (sys.argv).
    Example return values:
        $ sky launch app.yaml  # 'sky launch app.yaml'
        $ sky gpunode  # 'sky gpunode'
        $ python examples/app.py  # 'app.py'
    """
    argv = sys.argv
    basename = os.path.basename(argv[0])
    if basename == 'sky':
        # Turn '/.../anaconda/envs/py36/bin/sky' into 'sky', but keep other
        # things like 'examples/app.py'.
        argv[0] = basename
    return ' '.join(argv)


def get_user():
    return str(uuid.getnode())
