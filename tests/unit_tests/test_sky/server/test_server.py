"""Unit tests for the SkyPilot API server."""

import argparse
import asyncio
import threading
import time
from unittest import mock

import fastapi
import pytest
import uvicorn

from sky.server import server
from sky.server.requests import payloads
from sky.server.requests import requests as api_requests
from sky.utils import common_utils
from sky.utils import config_utils
from sky.utils import context


@mock.patch('uvicorn.run')
@mock.patch('sky.server.requests.executor.start')
@mock.patch('sky.utils.common_utils.get_cpu_count')
def test_deploy_flag_sets_workers_to_cpu_count(mock_get_cpu_count,
                                               mock_executor_start,
                                               mock_uvicorn_run):
    """Test that --deploy flag sets workers to CPU count."""
    # Setup
    mock_get_cpu_count.return_value = 8
    mock_executor_start.return_value = []

    # Create mock args with deploy=True
    test_args = argparse.Namespace(host='127.0.0.1', port=46580, deploy=True)

    # Call the main block with mocked args
    with mock.patch('argparse.ArgumentParser.parse_args',
                    return_value=test_args):
        with mock.patch('sky.server.requests.requests.reset_db_and_logs'):
            with mock.patch('sky.usage.usage_lib.maybe_show_privacy_policy'):
                # Execute the main block code directly
                num_workers = None
                if test_args.deploy:
                    num_workers = mock_get_cpu_count()

                workers = []
                try:
                    workers = mock_executor_start(test_args.deploy)
                    uvicorn.run('sky.server.server:app',
                                host=test_args.host,
                                port=test_args.port,
                                workers=num_workers)
                except Exception:
                    pass
                finally:
                    for worker in workers:
                        worker.terminate()

    # Verify that uvicorn.run was called with the correct number of workers
    mock_uvicorn_run.assert_called_once()
    call_args = mock_uvicorn_run.call_args[1]
    assert call_args['workers'] == 8
    assert call_args['host'] == '127.0.0.1'
    assert call_args['port'] == 46580


@mock.patch('uvicorn.run')
@mock.patch('sky.server.requests.executor.start')
def test_no_deploy_flag_uses_default_workers(mock_executor_start,
                                             mock_uvicorn_run):
    """Test that without --deploy flag, workers is None (default)."""
    # Setup
    mock_executor_start.return_value = []

    # Create mock args with deploy=False
    test_args = argparse.Namespace(host='127.0.0.1', port=46580, deploy=False)

    # Call the main block with mocked args
    with mock.patch('argparse.ArgumentParser.parse_args',
                    return_value=test_args):
        with mock.patch('sky.server.requests.requests.reset_db_and_logs'):
            with mock.patch('sky.usage.usage_lib.maybe_show_privacy_policy'):
                # Execute the main block code directly
                num_workers = None
                if test_args.deploy:
                    num_workers = common_utils.get_cpu_count()

                workers = []
                try:
                    workers = mock_executor_start(test_args.deploy)
                    uvicorn.run('sky.server.server:app',
                                host=test_args.host,
                                port=test_args.port,
                                workers=num_workers)
                except Exception:
                    pass
                finally:
                    for worker in workers:
                        worker.terminate()

    # Verify that uvicorn.run was called with workers=None
    mock_uvicorn_run.assert_called_once()
    call_args = mock_uvicorn_run.call_args[1]
    assert call_args['workers'] is None
    assert call_args['host'] == '127.0.0.1'
    assert call_args['port'] == 46580


@pytest.mark.asyncio
async def test_validate():
    """Test the validate endpoint."""
    mock_dag = mock.MagicMock()
    mock_validate_body = mock.MagicMock()
    mock_validate_body.dag = 'test_dag_yaml'
    mock_validate_body.request_options = {}

    with mock.patch('sky.server.server.dag_utils.load_chain_dag_from_yaml_str',
                   return_value=mock_dag), \
         mock.patch('sky.server.server.admin_policy_utils.apply',
                   return_value=(mock_dag, config_utils.Config())), \
         mock.patch.object(mock_dag, 'validate') as mock_validate:
        # Call validate endpoint
        await server.validate(mock_validate_body)
        # Verify validate was called with correct args
        mock_validate.assert_called_once_with(skip_file_mounts=True,
                                              skip_workdir=True)

    error_msg = 'Invalid DAG'
    with mock.patch('sky.server.server.dag_utils.load_chain_dag_from_yaml_str',
                   return_value=mock_dag), \
         mock.patch('sky.server.server.admin_policy_utils.apply',
                   return_value=(mock_dag, config_utils.Config())), \
         mock.patch.object(mock_dag, 'validate',
                          side_effect=ValueError(error_msg)):
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await server.validate(mock_validate_body)
        assert exc_info.value.status_code == 400
        assert error_msg in str(exc_info.value.detail)

    # Create an event to track when validation completes
    validation_complete = asyncio.Event()

    def slow_validate(*args, **kwargs):
        # Simulate slow validation
        time.sleep(0.1)
        validation_complete.set()

    with mock.patch('sky.server.server.dag_utils.load_chain_dag_from_yaml_str',
                   return_value=mock_dag), \
         mock.patch('sky.server.server.admin_policy_utils.apply',
                   return_value=(mock_dag, config_utils.Config())), \
         mock.patch.object(mock_dag, 'validate',
                          side_effect=slow_validate):
        # Start validation in background
        validation_task = asyncio.create_task(
            server.validate(mock_validate_body))

        # Check that validation hasn't completed immediately
        assert not validation_complete.is_set()

        # Wait for validation to complete
        await validation_task
        assert validation_complete.is_set()


@pytest.mark.asyncio
async def test_logs():
    """Test the logs endpoint."""
    mock_cluster_job_body = mock.MagicMock()
    mock_cluster_job_body.cluster_name = 'test-cluster'
    mock_background_tasks = mock.MagicMock()

    # Create an event to track when logs streaming starts
    streaming_started = threading.Event()

    # Mock the stream_response function
    def mock_stream_response(*args, **kwargs):
        streaming_started.set()
        return fastapi.responses.StreamingResponse(
            content=iter([]),  # Empty iterator for testing
            media_type='text/plain')

    def slow_execute(*args, **kwargs):
        # Simulate slow execution
        time.sleep(1)

    with mock.patch('sky.server.requests.executor.prepare_request') as mock_prepare, \
         mock.patch('sky.server.requests.executor.execute_request_coroutine',
                   side_effect=slow_execute) as mock_execute, \
         mock.patch('sky.server.stream_utils.stream_response',
                   side_effect=mock_stream_response) as mock_stream:

        # Mock prepare_request to return a request task
        mock_request_task = mock.MagicMock()
        mock_request_task.log_path = '/tmp/test.log'
        mock_prepare.return_value = mock_request_task

        # Start logs endpoint in background
        logs_task = asyncio.create_task(
            server.logs(mock.MagicMock(), mock_cluster_job_body,
                        mock_background_tasks))

        # Execute should be run in background and does not block streaming start
        streaming_started.wait(timeout=0.1)

        # Verify the response was created
        response = await logs_task
        assert isinstance(response, fastapi.responses.StreamingResponse)
        assert response.media_type == 'text/plain'

        # Verify the executor calls
        mock_prepare.assert_called_once()
        mock_execute.assert_called_once_with(mock_request_task)
        mock_stream.assert_called_once_with(
            request_id=mock.ANY,
            logs_path=mock_request_task.log_path,
            background_tasks=mock_background_tasks)


@pytest.mark.asyncio
async def test_job_status():
    """Test the jobs/job_status endpoint."""
    # Test without job_id filter
    mock_request = mock.MagicMock()
    mock_request.state.request_id = 'test-request-id'
    mock_job_status_body = payloads.JobsJobStatusBody()

    with mock.patch(
            'sky.server.requests.executor.schedule_request') as mock_schedule:
        # Call job_status endpoint without job_id
        await server.jobs_rest.job_status(mock_request, mock_job_status_body)

        # Verify the executor was called with correct parameters
        mock_schedule.assert_called_once_with(
            request_id='test-request-id',
            request_name='jobs.job_status',
            request_body=mock_job_status_body,
            func=mock.ANY,
            schedule_type=api_requests.ScheduleType.SHORT,
            request_cluster_name=mock.ANY)

    # Test with job_id filter
    mock_request = mock.MagicMock()
    mock_request.state.request_id = 'test-request-id-2'
    mock_job_status_body = payloads.JobsJobStatusBody(job_ids=[1, 2, 3])

    with mock.patch(
            'sky.server.requests.executor.schedule_request') as mock_schedule:
        # Call job_status endpoint with job_ids
        await server.jobs_rest.job_status(mock_request, mock_job_status_body)

        # Verify the executor was called with correct parameters
        mock_schedule.assert_called_once_with(
            request_id='test-request-id-2',
            request_name='jobs.job_status',
            request_body=mock_job_status_body,
            func=mock.ANY,
            schedule_type=api_requests.ScheduleType.SHORT,
            request_cluster_name=mock.ANY)

        # Verify the job_ids were passed correctly
        assert mock_job_status_body.job_ids == [1, 2, 3]
