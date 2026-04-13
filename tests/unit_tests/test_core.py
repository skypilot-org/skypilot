from unittest import mock

import pytest

from sky import core
from sky import exceptions
from sky import models
from sky.backends.cloud_vm_ray_backend import CloudVmRayResourceHandle
from sky.utils import common
from sky.utils import common_utils
from sky.utils import status_lib


@mock.patch('sky.backends.backend_utils.check_cluster_available')
@mock.patch('sky.backends.cloud_vm_ray_backend.CloudVmRayBackend.cancel_jobs')
def test_cancel_jobs_for_current_user(mock_cancel_jobs,
                                      mock_check_cluster_available) -> None:
    mock_handle = mock.create_autospec(CloudVmRayResourceHandle, instance=True)
    mock_check_cluster_available.return_value = mock_handle
    core.cancel('test-cluster', all=True)
    mock_cancel_jobs.assert_called_once_with(
        mock_handle,
        None,
        cancel_all=True,
        user_hash=common_utils.get_current_user().id,
    )


@mock.patch('sky.backends.backend_utils.check_cluster_available')
@mock.patch('sky.backends.cloud_vm_ray_backend.CloudVmRayBackend.cancel_jobs')
def test_cancel_jobs_for_all_users(mock_cancel_jobs,
                                   mock_check_cluster_available) -> None:
    mock_handle = mock.create_autospec(CloudVmRayResourceHandle, instance=True)
    mock_check_cluster_available.return_value = mock_handle
    core.cancel('test-cluster', all_users=True)
    mock_cancel_jobs.assert_called_once_with(
        mock_handle,
        None,
        cancel_all=True,
        user_hash=None,
    )


@mock.patch(
    'sky.backends.backend_utils.get_clusters',
    return_value=[
        {
            # properly formatted cluster record
            'name': 'test-cluster',
            'launched_at': '0',
            'handle': None,
            'last_use': 'sky launch',
            'status': status_lib.ClusterStatus.UP,
            'autostop': 0,
            'to_down': False,
            'cluster_hash': '00000',
            'cluster_ever_up': True,
            'status_updated_at': 0,
            'user_hash': '00000',
            'user_name': 'pilot',
            'workspace': 'default',
            'is_managed': False,
            'nodes': 0,
        },
        {
            # cluster record with missing fields
            'name': 'malformed-cluster',
        }
    ])
def test_status_best_effort(mock_get_clusters) -> None:
    with mock.patch('sky.core.logger') as mock_logger:
        core.status()
    mock_get_clusters.assert_called_once_with(
        refresh=common.StatusRefreshMode.NONE,
        cluster_names=None,
        all_users=False,
        include_credentials=False,
        summary_response=False,
        include_handle=True,
    )
    assert len(core.status()) == 1
    # Verify logging shows 0 cleaned requests
    mock_logger.warning.assert_called_once()
    log_message = mock_logger.warning.call_args[0][0]
    assert ('Failed to validate status responses for cluster malformed-cluster'
            in log_message)


class TestEnabledCloudsWorkspacePermission:
    """Tests for workspace permission check in core.enabled_clouds."""

    @mock.patch('sky.core.global_user_state.get_cached_enabled_clouds',
                return_value=[])
    @mock.patch('sky.core.workspaces_core.check_workspace_permission')
    def test_rejects_unauthorized_workspace(self, mock_check, _):
        mock_check.side_effect = exceptions.PermissionDeniedError('no access')
        mock_user = models.User(id='user-1', name='User1')
        with mock.patch('sky.core.common_utils.get_current_user',
                        return_value=mock_user):
            with pytest.raises(exceptions.PermissionDeniedError,
                               match='no access'):
                core.enabled_clouds(workspace='restricted')
        mock_check.assert_called_once_with(mock_user, 'restricted')

    @mock.patch('sky.core.global_user_state.get_cached_enabled_clouds',
                return_value=[])
    @mock.patch('sky.core.workspaces_core.check_workspace_permission')
    def test_skips_check_when_workspace_is_none(self, mock_check, _):
        """When workspace is None, falls back to active workspace
        and does not call check_workspace_permission."""
        with mock.patch('sky.core.skypilot_config.get_active_workspace',
                        return_value='default'), \
             mock.patch('sky.core.skypilot_config.local_active_workspace_ctx'):
            core.enabled_clouds(workspace=None)
        mock_check.assert_not_called()
