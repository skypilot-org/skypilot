"""Utils for usage logging."""

import hashlib
import time
import uuid

from sky.utils import common_utils

_run_id = None


def _get_logging_run_id():
    """Returns a unique run id for this logging."""
    global _run_id
    if _run_id is None:
        _run_id = str(uuid.uuid4())
    return _run_id


def _get_logging_user_hash():
    """Returns a unique user-machine specific hash as a user id."""
    hash_str = common_utils.user_and_hostname_hash()
    return hashlib.md5(hash_str.encode()).hexdigest()[:8]


def get_base_labels():
    """Returns a dict of common base labels for logs."""
    labels = {
        'user': _get_logging_user_hash(),
        'run_id': _get_logging_run_id(),
        'time': time.time(),
    }
    return labels
