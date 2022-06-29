"""Utils for logging and metrics."""

import time

from sky.utils import base_utils


def get_base_labels():
    labels = {
        'user': base_utils.get_user(),
        'transaction_id': base_utils.transaction_id(),
        'time': time.time(),
    }
    return labels
