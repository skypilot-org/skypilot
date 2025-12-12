"""Tests for CLI helper functions.

This module contains tests for CLI helper functions in sky.client.cli.command.
"""
import traceback
from typing import Any, Dict, List, Optional, Tuple, Union
from unittest import mock

import click
import colorama
import pytest

from sky import exceptions
from sky import jobs as managed_jobs
from sky.client import sdk as client_sdk
from sky.client.cli import command
from sky.client.cli import table_utils
from sky.client.cli import utils as cli_utils
from sky.schemas.api import responses
from sky.server import common as server_common
from sky.skylet import constants
from sky.usage import usage_lib
from sky.utils import common as sky_common
from sky.utils import common_utils
from sky.utils import controller_utils
from sky.utils import env_options
from sky.utils import status_lib


def test_handle_jobs_queue_request_success_tuple_response():
    """Test _handle_jobs_queue_request with tuple response (v2 API)."""
    # Create mock managed job records
    mock_job_1 = responses.ManagedJobRecord(
        job_id=1,
        job_name='test-job-1',
        status=managed_jobs.ManagedJobStatus.RUNNING,
    )
    mock_job_2 = responses.ManagedJobRecord(
        job_id=2,
        job_name='test-job-2',
        status=managed_jobs.ManagedJobStatus.SUCCEEDED,
    )

    managed_jobs_list = [mock_job_1, mock_job_2]
    status_counts = {
        'RUNNING': 1,
        'SUCCEEDED': 1,
    }

    # Mock the result as a tuple (v2 API)
    mock_result = (managed_jobs_list, 2, status_counts, 0)

    request_id = server_common.RequestId[
        Union[List[responses.ManagedJobRecord],
              Tuple[List[responses.ManagedJobRecord], int, Dict[str, int],
                    int]]]('test-request-id')

    with mock.patch.object(client_sdk,
                           'stream_and_get',
                           return_value=mock_result) as mock_stream:
        with mock.patch.object(usage_lib.messages.usage, 'set_internal'):
            with mock.patch.object(
                    table_utils, 'format_job_table',
                    return_value='formatted table') as mock_format:
                num_jobs, msg = command._handle_jobs_queue_request(
                    request_id=request_id,
                    show_all=False,
                    show_user=False,
                    max_num_jobs_to_show=10,
                    is_called_by_user=False,
                    only_in_progress=False,
                    queue_result_version=cli_utils.QueueResultVersion.V2,
                )

    # Verify the result
    assert num_jobs == 2  # Total number of jobs
    assert msg == 'formatted table'
    mock_stream.assert_called_once_with(request_id)
    mock_format.assert_called_once_with(
        managed_jobs_list,
        pool_status=None,
        show_all=False,
        show_user=False,
        max_jobs=10,
        status_counts=status_counts,
    )


def test_handle_jobs_queue_request_success_list_response():
    """Test _handle_jobs_queue_request with list response (legacy API)."""
    # Create mock managed job records as dicts
    mock_jobs = [
        {
            'job_id': 1,
            'job_name': 'test-job-1'
        },
        {
            'job_id': 2,
            'job_name': 'test-job-2'
        },
        {
            'job_id': 3,
            'job_name': 'test-job-3'
        },
    ]

    # Mock job records using the model
    mock_job_records = [responses.ManagedJobRecord(**job) for job in mock_jobs]

    request_id = server_common.RequestId[List[responses.ManagedJobRecord]](
        'test-request-id')

    with mock.patch.object(client_sdk,
                           'stream_and_get',
                           return_value=mock_job_records) as mock_stream:
        with mock.patch.object(usage_lib.messages.usage, 'set_internal'):
            with mock.patch.object(
                    table_utils, 'format_job_table',
                    return_value='formatted table') as mock_format:
                num_jobs, msg = command._handle_jobs_queue_request(
                    request_id=request_id,
                    show_all=True,
                    show_user=True,
                    max_num_jobs_to_show=None,
                    is_called_by_user=True,
                    only_in_progress=False,
                    queue_result_version=cli_utils.QueueResultVersion.V1,
                )

    # Verify the result - should count unique job IDs
    assert num_jobs == 3
    assert msg == 'formatted table'
    mock_stream.assert_called_once_with(request_id)
    mock_format.assert_called_once_with(
        mock_job_records,
        pool_status=None,
        show_all=True,
        show_user=True,
        max_jobs=None,
        status_counts=None,
    )


def test_handle_jobs_queue_request_success_list_response_with_pool_status():
    """Test _handle_jobs_queue_request with list response (legacy API)."""
    # Create mock managed job records as dicts
    mock_jobs = [
        {
            'job_id': 1,
            'job_name': 'test-job-1'
        },
        {
            'job_id': 2,
            'job_name': 'test-job-2'
        },
        {
            'job_id': 3,
            'job_name': 'test-job-3'
        },
    ]

    # Mock job records using the model
    mock_job_records = [responses.ManagedJobRecord(**job) for job in mock_jobs]

    # Mock pool status records using the model
    mock_pool_statuses = [
        {
            'replica_info': [
                {
                    'replica_id': 1,
                    'used_by': 3,
                },
                {
                    'replica_id': 2,
                    'used_by': 2,
                },
                {
                    'replica_id': 3,
                    'used_by': 1,
                },
            ],
        },
    ]

    request_id = server_common.RequestId[List[responses.ManagedJobRecord]](
        'test-request-id')

    pool_status_request_id = server_common.RequestId[List[Dict[str, Any]]](
        'test-pool-status-request-id')

    with mock.patch.object(client_sdk,
                           'stream_and_get',
                           side_effect=[mock_job_records,
                                        mock_pool_statuses]) as mock_stream:
        with mock.patch.object(usage_lib.messages.usage, 'set_internal'):
            with mock.patch.object(
                    table_utils, 'format_job_table',
                    return_value='formatted table') as mock_format:
                num_jobs, msg = command._handle_jobs_queue_request(
                    request_id=request_id,
                    show_all=True,
                    show_user=True,
                    max_num_jobs_to_show=None,
                    pool_status_request_id=pool_status_request_id,
                    is_called_by_user=True,
                    only_in_progress=False,
                )

    # Verify the result - should count unique job IDs
    assert num_jobs == 3
    assert msg == 'formatted table'
    mock_stream.assert_has_calls([
        mock.call(request_id),
        mock.call(pool_status_request_id),
    ])
    mock_format.assert_called_once_with(
        mock_job_records,
        pool_status=mock_pool_statuses,
        show_all=True,
        show_user=True,
        max_jobs=None,
        status_counts=None,
    )


def test_handle_jobs_queue_request_cluster_not_up_error():
    """Test _handle_jobs_queue_request with ClusterNotUpError."""
    request_id = server_common.RequestId[List[responses.ManagedJobRecord]](
        'test-request-id')

    error_msg = 'Jobs controller is not up'
    with mock.patch.object(client_sdk, 'stream_and_get') as mock_stream:
        mock_stream.side_effect = exceptions.ClusterNotUpError(
            error_msg, cluster_status=None)

        with mock.patch.object(usage_lib.messages.usage, 'set_internal'):
            num_jobs, msg = command._handle_jobs_queue_request(
                request_id=request_id,
                show_all=False,
                show_user=False,
                max_num_jobs_to_show=10,
                is_called_by_user=False,
                only_in_progress=False,
            )

    # Verify error handling
    assert num_jobs is None
    assert error_msg in msg
    assert 'sky jobs -h' in msg


def test_handle_jobs_queue_request_cluster_stopped():
    """Test _handle_jobs_queue_request with stopped controller."""
    request_id = server_common.RequestId[List[responses.ManagedJobRecord]](
        'test-request-id')

    error_msg = 'Jobs controller is stopped'
    with mock.patch.object(client_sdk, 'stream_and_get') as mock_stream:
        mock_stream.side_effect = exceptions.ClusterNotUpError(
            error_msg, cluster_status=status_lib.ClusterStatus.STOPPED)

        with mock.patch.object(usage_lib.messages.usage, 'set_internal'):
            num_jobs, msg = command._handle_jobs_queue_request(
                request_id=request_id,
                show_all=False,
                show_user=False,
                max_num_jobs_to_show=10,
                is_called_by_user=True,
                only_in_progress=False,
            )

    # Verify error handling with stopped controller
    assert num_jobs is None
    assert error_msg in msg
    assert 'sky jobs queue --refresh' in msg


def test_handle_jobs_queue_request_runtime_error_controller_stopped():
    """Test _handle_jobs_queue_request with RuntimeError and stopped controller."""
    request_id = server_common.RequestId[List[responses.ManagedJobRecord]](
        'test-request-id')

    # Mock controller record showing STOPPED status
    controller_record = {
        'status': status_lib.ClusterStatus.STOPPED,
    }

    with mock.patch.object(client_sdk, 'stream_and_get') as mock_stream:
        mock_stream.side_effect = RuntimeError('Connection failed')

        with mock.patch.object(usage_lib.messages.usage, 'set_internal'):
            with mock.patch.object(client_sdk, 'status') as mock_status:
                with mock.patch.object(client_sdk,
                                       'get',
                                       return_value=[controller_record]):
                    num_jobs, msg = command._handle_jobs_queue_request(
                        request_id=request_id,
                        show_all=False,
                        show_user=False,
                        max_num_jobs_to_show=10,
                        is_called_by_user=False,
                        only_in_progress=False,
                    )

    # Verify error handling
    assert num_jobs is None
    # Should show the controller's default hint
    controller = controller_utils.Controllers.JOBS_CONTROLLER.value
    assert controller.default_hint_if_non_existent in msg


def test_handle_jobs_queue_request_runtime_error_no_records():
    """Test _handle_jobs_queue_request with RuntimeError and no controller records."""
    request_id = server_common.RequestId[List[responses.ManagedJobRecord]](
        'test-request-id')

    with mock.patch.object(client_sdk, 'stream_and_get') as mock_stream:
        mock_stream.side_effect = RuntimeError('Connection failed')

        with mock.patch.object(usage_lib.messages.usage, 'set_internal'):
            with mock.patch.object(client_sdk, 'status') as mock_status:
                with mock.patch.object(client_sdk, 'get', return_value=[]):
                    num_jobs, msg = command._handle_jobs_queue_request(
                        request_id=request_id,
                        show_all=False,
                        show_user=False,
                        max_num_jobs_to_show=10,
                        is_called_by_user=False,
                        only_in_progress=False,
                    )

    # Verify error handling
    assert num_jobs is None
    # Should show the controller's default hint
    controller = controller_utils.Controllers.JOBS_CONTROLLER.value
    assert controller.default_hint_if_non_existent in msg


def test_handle_jobs_queue_request_runtime_error_fallback():
    """Test _handle_jobs_queue_request with RuntimeError and failed controller check."""
    request_id = server_common.RequestId[List[responses.ManagedJobRecord]](
        'test-request-id')

    error = RuntimeError('Connection failed')
    with mock.patch.object(client_sdk, 'stream_and_get') as mock_stream:
        mock_stream.side_effect = error

        with mock.patch.object(usage_lib.messages.usage, 'set_internal'):
            with mock.patch.object(
                    client_sdk,
                    'status',
                    side_effect=Exception('Status check failed')):
                num_jobs, msg = command._handle_jobs_queue_request(
                    request_id=request_id,
                    show_all=False,
                    show_user=False,
                    max_num_jobs_to_show=10,
                    is_called_by_user=False,
                    only_in_progress=False,
                )

    # Verify error handling with fallback message
    assert num_jobs is None
    assert 'Failed to query managed jobs due to connection issues' in msg
    assert 'Connection failed' in msg


def test_handle_jobs_queue_request_generic_exception_with_debug():
    """Test _handle_jobs_queue_request with generic exception and debug enabled."""
    request_id = server_common.RequestId[List[responses.ManagedJobRecord]](
        'test-request-id')

    error = ValueError('Some error occurred')
    with mock.patch.object(client_sdk, 'stream_and_get') as mock_stream:
        mock_stream.side_effect = error

        with mock.patch.object(usage_lib.messages.usage, 'set_internal'):
            with mock.patch.object(env_options.Options.SHOW_DEBUG_INFO,
                                   'get',
                                   return_value=True):
                num_jobs, msg = command._handle_jobs_queue_request(
                    request_id=request_id,
                    show_all=False,
                    show_user=False,
                    max_num_jobs_to_show=10,
                    is_called_by_user=False,
                    only_in_progress=False,
                )

    # Verify error handling with debug info
    assert num_jobs is None
    assert 'Failed to query managed jobs:' in msg
    assert 'Some error occurred' in msg
    # Should contain traceback when debug is enabled
    assert 'Traceback' in msg


def test_handle_jobs_queue_request_generic_exception_no_debug():
    """Test _handle_jobs_queue_request with generic exception and debug disabled."""
    request_id = server_common.RequestId[List[responses.ManagedJobRecord]](
        'test-request-id')

    error = ValueError('Some error occurred')
    with mock.patch.object(client_sdk, 'stream_and_get') as mock_stream:
        mock_stream.side_effect = error

        with mock.patch.object(usage_lib.messages.usage, 'set_internal'):
            with mock.patch.object(env_options.Options.SHOW_DEBUG_INFO,
                                   'get',
                                   return_value=False):
                num_jobs, msg = command._handle_jobs_queue_request(
                    request_id=request_id,
                    show_all=False,
                    show_user=False,
                    max_num_jobs_to_show=10,
                    is_called_by_user=False,
                    only_in_progress=False,
                )

    # Verify error handling without debug info
    assert num_jobs is None
    assert 'Failed to query managed jobs:' in msg
    assert 'Some error occurred' in msg
    # Should NOT contain traceback when debug is disabled
    assert 'Traceback' not in msg


def test_handle_jobs_queue_request_sets_internal_when_not_called_by_user():
    """Test that set_internal is called when is_called_by_user is False."""
    request_id = server_common.RequestId[List[responses.ManagedJobRecord]](
        'test-request-id')

    mock_jobs = [responses.ManagedJobRecord(job_id=1, job_name='test-job')]

    with mock.patch.object(client_sdk, 'stream_and_get',
                           return_value=mock_jobs):
        with mock.patch.object(usage_lib.messages.usage,
                               'set_internal') as mock_set_internal:
            with mock.patch.object(table_utils,
                                   'format_job_table',
                                   return_value='table'):
                command._handle_jobs_queue_request(
                    request_id=request_id,
                    show_all=False,
                    show_user=False,
                    max_num_jobs_to_show=10,
                    is_called_by_user=False,
                    only_in_progress=False,
                )

    # set_internal should be called
    mock_set_internal.assert_called_once()


def test_handle_jobs_queue_request_does_not_set_internal_when_called_by_user():
    """Test that set_internal is not called when is_called_by_user is True."""
    request_id = server_common.RequestId[List[responses.ManagedJobRecord]](
        'test-request-id')

    mock_jobs = [responses.ManagedJobRecord(job_id=1, job_name='test-job')]

    with mock.patch.object(client_sdk, 'stream_and_get',
                           return_value=mock_jobs):
        with mock.patch.object(usage_lib.messages.usage,
                               'set_internal') as mock_set_internal:
            with mock.patch.object(table_utils,
                                   'format_job_table',
                                   return_value='table'):
                command._handle_jobs_queue_request(
                    request_id=request_id,
                    show_all=False,
                    show_user=False,
                    max_num_jobs_to_show=10,
                    is_called_by_user=True,
                    only_in_progress=False,
                )

    # set_internal should NOT be called
    mock_set_internal.assert_not_called()


def test_handle_jobs_queue_request_counts_terminal_status_correctly():
    """Test that terminal job statuses are not counted as in-progress."""
    # Create jobs with various statuses
    mock_jobs = [
        responses.ManagedJobRecord(
            job_id=1,
            job_name='job-1',
            status=managed_jobs.ManagedJobStatus.RUNNING),
        responses.ManagedJobRecord(
            job_id=2,
            job_name='job-2',
            status=managed_jobs.ManagedJobStatus.PENDING),
        responses.ManagedJobRecord(
            job_id=3,
            job_name='job-3',
            status=managed_jobs.ManagedJobStatus.SUCCEEDED),
        responses.ManagedJobRecord(job_id=4,
                                   job_name='job-4',
                                   status=managed_jobs.ManagedJobStatus.FAILED),
        responses.ManagedJobRecord(
            job_id=5,
            job_name='job-5',
            status=managed_jobs.ManagedJobStatus.CANCELLED),
    ]

    status_counts = {
        'RUNNING': 1,
        'PENDING': 1,
        'SUCCEEDED': 1,
        'FAILED': 1,
        'CANCELLED': 1,
    }

    mock_result = (mock_jobs, 5, status_counts, 0)

    request_id = server_common.RequestId[
        Union[List[responses.ManagedJobRecord],
              Tuple[List[responses.ManagedJobRecord], int, Dict[str, int],
                    int]]]('test-request-id')

    with mock.patch.object(client_sdk,
                           'stream_and_get',
                           return_value=mock_result):
        with mock.patch.object(usage_lib.messages.usage, 'set_internal'):
            with mock.patch.object(table_utils,
                                   'format_job_table',
                                   return_value='table'):
                num_jobs, msg = command._handle_jobs_queue_request(
                    request_id=request_id,
                    show_all=False,
                    show_user=False,
                    max_num_jobs_to_show=10,
                    is_called_by_user=False,
                    only_in_progress=False,
                    queue_result_version=cli_utils.QueueResultVersion.V2,
                )

    # Should return the total number of jobs (5) when only_in_progress=False
    assert num_jobs == 5


def test_handle_jobs_queue_request_only_in_progress_true():
    """Test _handle_jobs_queue_request with only_in_progress=True."""
    # Create jobs with various statuses
    mock_jobs = [
        responses.ManagedJobRecord(
            job_id=1,
            job_name='job-1',
            status=managed_jobs.ManagedJobStatus.RUNNING),
        responses.ManagedJobRecord(
            job_id=2,
            job_name='job-2',
            status=managed_jobs.ManagedJobStatus.PENDING),
        responses.ManagedJobRecord(
            job_id=3,
            job_name='job-3',
            status=managed_jobs.ManagedJobStatus.SUCCEEDED),
        responses.ManagedJobRecord(job_id=4,
                                   job_name='job-4',
                                   status=managed_jobs.ManagedJobStatus.FAILED),
        responses.ManagedJobRecord(
            job_id=5,
            job_name='job-5',
            status=managed_jobs.ManagedJobStatus.CANCELLED),
    ]

    status_counts = {
        'RUNNING': 1,
        'PENDING': 1,
        'SUCCEEDED': 1,
        'FAILED': 1,
        'CANCELLED': 1,
    }

    mock_result = (mock_jobs, 5, status_counts, 0)

    request_id = server_common.RequestId[
        Union[List[responses.ManagedJobRecord],
              Tuple[List[responses.ManagedJobRecord], int, Dict[str, int],
                    int]]]('test-request-id')

    with mock.patch.object(client_sdk,
                           'stream_and_get',
                           return_value=mock_result):
        with mock.patch.object(usage_lib.messages.usage, 'set_internal'):
            with mock.patch.object(table_utils,
                                   'format_job_table',
                                   return_value='table'):
                num_jobs, msg = command._handle_jobs_queue_request(
                    request_id=request_id,
                    show_all=False,
                    show_user=False,
                    max_num_jobs_to_show=10,
                    is_called_by_user=False,
                    only_in_progress=True,
                    queue_result_version=cli_utils.QueueResultVersion.V2,
                )

    # Only RUNNING and PENDING should be counted as in-progress (non-terminal)
    assert num_jobs == 2


def test_handle_jobs_queue_request_only_in_progress_with_no_status_counts():
    """Test _handle_jobs_queue_request with only_in_progress=True but no status_counts."""
    # Create a tuple response with empty status_counts
    mock_jobs = [
        responses.ManagedJobRecord(
            job_id=1,
            job_name='job-1',
            status=managed_jobs.ManagedJobStatus.RUNNING),
    ]

    mock_result = (mock_jobs, 1, None, 0)  # status_counts is None

    request_id = server_common.RequestId[
        Union[List[responses.ManagedJobRecord],
              Tuple[List[responses.ManagedJobRecord], int, Dict[str, int],
                    int]]]('test-request-id')

    with mock.patch.object(client_sdk,
                           'stream_and_get',
                           return_value=mock_result):
        with mock.patch.object(usage_lib.messages.usage, 'set_internal'):
            with mock.patch.object(table_utils,
                                   'format_job_table',
                                   return_value='table'):
                num_jobs, msg = command._handle_jobs_queue_request(
                    request_id=request_id,
                    show_all=False,
                    show_user=False,
                    max_num_jobs_to_show=10,
                    is_called_by_user=False,
                    only_in_progress=True,
                    queue_result_version=cli_utils.QueueResultVersion.V2,
                )

    # Should return 0 when status_counts is None
    assert num_jobs == 0


def test_handle_jobs_queue_request_only_in_progress_all_terminal():
    """Test _handle_jobs_queue_request with only_in_progress=True and all terminal jobs."""
    # Create jobs with only terminal statuses
    mock_jobs = [
        responses.ManagedJobRecord(
            job_id=1,
            job_name='job-1',
            status=managed_jobs.ManagedJobStatus.SUCCEEDED),
        responses.ManagedJobRecord(job_id=2,
                                   job_name='job-2',
                                   status=managed_jobs.ManagedJobStatus.FAILED),
        responses.ManagedJobRecord(
            job_id=3,
            job_name='job-3',
            status=managed_jobs.ManagedJobStatus.CANCELLED),
    ]

    status_counts = {
        'SUCCEEDED': 1,
        'FAILED': 1,
        'CANCELLED': 1,
    }

    mock_result = (mock_jobs, 3, status_counts, 0)

    request_id = server_common.RequestId[
        Union[List[responses.ManagedJobRecord],
              Tuple[List[responses.ManagedJobRecord], int, Dict[str, int],
                    int]]]('test-request-id')

    with mock.patch.object(client_sdk,
                           'stream_and_get',
                           return_value=mock_result):
        with mock.patch.object(usage_lib.messages.usage, 'set_internal'):
            with mock.patch.object(table_utils,
                                   'format_job_table',
                                   return_value='table'):
                num_jobs, msg = command._handle_jobs_queue_request(
                    request_id=request_id,
                    show_all=False,
                    show_user=False,
                    max_num_jobs_to_show=10,
                    is_called_by_user=False,
                    only_in_progress=True,
                    queue_result_version=cli_utils.QueueResultVersion.V2,
                )

    # Should return 0 since all jobs are terminal
    assert num_jobs == 0


def test_natural_order_group_list_commands_hides_aliases_and_hidden():
    """list_commands should hide duplicate command objects and hidden commands."""
    group = command._NaturalOrderGroup()

    base_cmd = click.Command('volumes')
    group.add_command(base_cmd, name='volumes')
    group.add_command(base_cmd, name='volume')  # alias pointing to same object

    hidden_cmd = click.Command('hidden', hidden=True)
    group.add_command(hidden_cmd, name='hidden')

    other_cmd = click.Command('other')
    group.add_command(other_cmd, name='other')

    assert group.list_commands(ctx=None) == ['volumes', 'other']
