"""The health check must count live nodes, not `ray status` text.

Ray 2.56.1 leaves a node under `Active:` after the GCS has marked it dead
(measured: still listed 174s later), so counting `ray status` lines gets node
loss wrong in both directions while the cloud still reports every machine up.
"""
import re
import shlex
import time
from unittest import mock

from sky import backends
from sky import clouds
from sky.backends import backend_utils
from sky.provision import instance_setup
from sky.utils import status_lib

_UP = status_lib.ClusterStatus.UP


def _ray_status_text(n_active):
    nodes = '\n'.join(f' 1 node_{i:056x}' for i in range(n_active))
    return (f'Node status\n{"-" * 63}\nActive:\n{nodes}\nPending:\n'
            ' (no pending nodes)\nRecent failures:\n (no failures)\n')


def _refresh(gcs_alive, gcs_total, ray_status_nodes):
    """Refresh a 2-node cluster whose 2 pods are both up.

    The fake head answers the GCS count and `ray status` separately, so the
    result shows which one the health check believed.
    """

    def _run(cmd, **kwargs):
        del kwargs
        if 'SKY_ALIVE_NODES' in cmd:
            return 0, f'SKY_ALIVE_NODES {gcs_alive} {gcs_total}\n', ''
        return 0, _ray_status_text(ray_status_nodes), ''

    head = mock.Mock()
    head.run.side_effect = _run
    handle = mock.Mock(spec=backends.CloudVmRayResourceHandle)
    handle.cluster_name = 'test-cluster'
    handle.cluster_name_on_cloud = 'test-cluster-1234'
    handle.cluster_yaml = '/fake/path/cluster.yaml'
    handle.cached_cluster_info = None
    handle.launched_nodes = 2
    handle.num_ips_per_node = 1
    handle.head_ip = '10.0.0.1'
    handle.launched_resources = mock.Mock(unsafe=True)
    handle.launched_resources.cloud = clouds.Kubernetes()
    handle.launched_resources.use_spot = False
    handle.launched_resources.assert_launchable.return_value = (
        handle.launched_resources)
    handle.provision_runtime_metadata = mock.Mock()
    handle.provision_runtime_metadata.has_ray = True
    handle.get_command_runners.return_value = [head, mock.Mock()]
    record = {
        'handle': handle,
        'status': _UP,
        'cluster_hash': 'fake-hash',
        'autostop': -1,
        'to_down': False,
        'launched_at': time.time() - 3600,
    }
    backend = mock.Mock(spec=backends.CloudVmRayBackend)
    backend.is_definitely_autostopping.return_value = False
    external_failure = mock.Mock()
    external_failure.get.return_value = None
    add_or_update = mock.Mock()
    now = [0.0]

    def _sleep(seconds):
        now[0] += seconds

    with mock.patch.object(backend_utils,
                           '_query_cluster_status_via_cloud_api',
                           return_value={
                               'pod-0': (_UP, None),
                               'pod-1': (_UP, None)
                           }), \
         mock.patch.object(backend_utils, 'ExternalFailureSource',
                           external_failure), \
         mock.patch.object(backend_utils, 'get_backend_from_handle',
                           return_value=backend), \
         mock.patch.object(backend_utils.global_user_state,
                           'add_cluster_event'), \
         mock.patch.object(backend_utils.global_user_state,
                           'add_or_update_cluster', add_or_update), \
         mock.patch.object(backend_utils.global_user_state,
                           'get_cluster_from_name', return_value=record), \
         mock.patch.object(backend_utils.time, 'sleep', side_effect=_sleep), \
         mock.patch.object(backend_utils.time, 'monotonic',
                           side_effect=lambda: now[0]):
        backend_utils._update_cluster_status('test-cluster',
                                             record,
                                             retry_if_missing=False)
    return add_or_update.call_args.kwargs['ready']


def test_replaced_worker_with_a_dead_entry_left_is_up():
    """A resize-down or a replaced worker: 2 live nodes, 3 listed.

    The `test_resize on kubernetes` failure: counting `ray status` lines saw
    3 of 2 and set the cluster to INIT.
    """
    assert _refresh(gcs_alive=2, gcs_total=3, ray_status_nodes=3) is True


def test_worker_whose_ray_died_is_not_up():
    """The worker's raylet died while its machine stayed up: 1 live node.

    `ray status` still lists it, so counting lines saw 2 of 2 and kept the
    cluster UP with a dead worker.
    """
    assert _refresh(gcs_alive=1, gcs_total=2, ray_status_nodes=2) is False


def test_the_command_asks_the_gcs_not_ray_status():
    cmd = instance_setup.RAY_ALIVE_NODE_COUNT_COMMAND
    assert 'ray.nodes()' in cmd and '"Alive"' in cmd
    assert 'ray status' not in cmd, (
        'the count must not come from `ray status` again')
    # It is spliced into a shell command, so it has to survive the shell.
    assert shlex.split(cmd)


def test_the_head_wait_does_not_depend_on_a_ray_message():
    """2.9.3 printed "No cluster status."; 2.56.1 raises instead.

    Keyed on a message, the wait silently became a no-op on 2.56.1.
    """
    cmd = instance_setup.RAY_HEAD_WAIT_INITIALIZED_COMMAND
    assert 'No cluster status.' not in cmd
    assert re.search(r'status\s*>\s*/dev/null 2>&1 && break', cmd), cmd
    # Bounded: a failing `ray status` takes ~35s to return on 2.56.1.
    assert '_sky_deadline' in cmd
    assert shlex.split(cmd)
