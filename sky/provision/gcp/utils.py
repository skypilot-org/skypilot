from typing import List, Union, Tuple, Optional

import functools
import re
import time
import uuid

# Tag for the name of the node
TAG_RAY_NODE_NAME = "ray-node-name"
# Tag uniquely identifying all nodes of a cluster
TAG_RAY_CLUSTER_NAME = "ray-cluster-name"

MAX_POLLS = 12
# TPUs take a long while to respond, so we increase the MAX_POLLS
# considerably - this probably could be smaller
# TPU deletion uses MAX_POLLS
MAX_POLLS_TPU = MAX_POLLS * 8
# Stopping instances can take several minutes, so we increase the timeout
MAX_POLLS_STOP = MAX_POLLS * 8
POLL_INTERVAL = 5

INSTANCE_NAME_MAX_LEN = 64
INSTANCE_NAME_UUID_LEN = 8


def retry_on_exception(
    exception: Union[Exception, Tuple[Exception]],
    regex: Optional[str] = None,
    max_retries: int = MAX_POLLS,
    retry_interval_s: int = POLL_INTERVAL,
):
    """Retry a function call n-times for as long as it throws an exception."""

    def dec(func):

        @functools.wraps(func)
        def wrapper(*args, **kwargs):

            def try_catch_exc():
                try:
                    value = func(*args, **kwargs)
                    return value
                except Exception as e:
                    if not isinstance(e, exception) or (
                            regex and not re.search(regex, str(e))):
                        raise e
                    return e

            for _ in range(max_retries):
                ret = try_catch_exc()
                if not isinstance(ret, Exception):
                    break
                time.sleep(retry_interval_s)
            if isinstance(ret, Exception):
                raise ret
            return ret

        return wrapper

    return dec


def get_zones_from_regions(region: str, project_id: str,
                           compute_client) -> List[str]:
    response = compute_client.zones().list(
        project=project_id, filter=f'name eq "^{region}-.*"').execute()
    return sorted([zone['name'] for zone in response.get('items', [])])


def generate_node_name(cluster_name: str, node_suffix: str) -> str:
    """Generate node name from labels and suffix.

    This is required so that the correct resource can be selected
    when the only information autoscaler has is the name of the node.

    The suffix is expected to be one of 'compute' or 'tpu'
    (as in ``GCPNodeType``).
    """
    name_label = cluster_name
    if len(name_label) > INSTANCE_NAME_MAX_LEN - INSTANCE_NAME_UUID_LEN - 1:
        raise ValueError(
            f'Instance name ({name_label}) too long ({len(name_label)})')
    uid = uuid.uuid4().hex[:INSTANCE_NAME_UUID_LEN]
    return f'{name_label}-{uid}-{node_suffix}'
