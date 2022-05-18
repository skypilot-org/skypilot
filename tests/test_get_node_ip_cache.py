import pytest

import sky
from sky import backends
from sky import exceptions
from sky import global_user_state
from sky.backends import backend_utils


def test_head_ip_cache_invalidated():
    # This unittest ensures 'get_node_ips' will error with the
    # cached IP but actually the IP is
    cluster_name = 'test-head-ip-cache-invalidated'
    handle = backends.CloudVmRayBackend.ResourceHandle(
        cluster_name=cluster_name,
        cluster_yaml='/tmp/cluster1.yaml',
        head_ip='169.254.0.0',
        launched_nodes=1,
        launched_resources=sky.Resources(sky.AWS(),
                                         instance_type='p3.2xlarge',
                                         region='us-east-1'),
    )
    global_user_state.add_or_update_cluster(cluster_name, handle, ready=True)
    with pytest.raises(exceptions.FetchIPError):
        backend_utils.get_node_ips('',
                                   1,
                                   cluster_name=cluster_name,
                                   check_cached_ips=True)
    global_user_state.remove_cluster(cluster_name, terminate=True)
