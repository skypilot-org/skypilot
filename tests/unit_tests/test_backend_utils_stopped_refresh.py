"""Tests for refreshing a cluster whose nodes are all stopped."""
from unittest import mock

from sky import backends
from sky import clouds
from sky.backends import backend_utils
from sky.utils import status_lib


def _handle():
    handle = mock.Mock(spec=backends.CloudVmRayResourceHandle)
    handle.cluster_name = 'c'
    handle.cluster_name_on_cloud = 'c-1234'
    handle.cluster_yaml = '/fake/cluster.yaml'
    handle.launched_nodes = 2
    handle.launched_resources = mock.Mock(unsafe=True)
    handle.launched_resources.cloud = clouds.AWS()
    handle.launched_resources.use_spot = False
    handle.launched_resources.assert_launchable.return_value = (
        handle.launched_resources)
    handle.num_ips_per_node = 1
    handle.provision_runtime_metadata = mock.Mock()
    handle.provision_runtime_metadata.has_ray = True
    return handle


def _refresh(status: status_lib.ClusterStatus):
    """Refreshes a cluster recorded as `status` whose nodes are stopped."""
    record = {
        'handle': _handle(),
        'status': status,
        'cluster_hash': 'hash-1',
        'autostop': -1,
        'to_down': False,
        'launched_at': 0,
    }
    stopped = (status_lib.ClusterStatus.STOPPED, None)
    external_failure = mock.Mock()
    external_failure.get.return_value = None
    gus = backend_utils.global_user_state
    with mock.patch.object(backend_utils,
                           '_query_cluster_status_via_cloud_api',
                           return_value={'c-head': stopped,
                                         'c-worker': stopped}), \
         mock.patch.object(backend_utils, 'ExternalFailureSource',
                           external_failure), \
         mock.patch.object(gus, 'add_cluster_event') as add_event, \
         mock.patch.object(gus, 'set_cluster_status') as set_status, \
         mock.patch.object(gus, 'get_cluster_from_name') as get_record, \
         mock.patch.object(backends.CloudVmRayBackend,
                           'post_teardown_cleanup') as cleanup:
        result = backend_utils._update_cluster_status('c',
                                                      record,
                                                      retry_if_missing=False)
    assert result is get_record.return_value
    return add_event, set_status, cleanup


def test_still_stopped_cluster_only_records_the_check():
    add_event, set_status, cleanup = _refresh(status_lib.ClusterStatus.STOPPED)
    set_status.assert_called_once_with('c', status_lib.ClusterStatus.STOPPED)
    cleanup.assert_not_called()
    add_event.assert_not_called()


def test_newly_stopped_cluster_is_cleaned_up():
    add_event, set_status, cleanup = _refresh(status_lib.ClusterStatus.UP)
    cleanup.assert_called_once()
    assert cleanup.call_args.kwargs['terminate'] is False
    add_event.assert_called_once()
    set_status.assert_not_called()
