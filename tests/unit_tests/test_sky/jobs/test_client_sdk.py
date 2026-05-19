"""Unit tests for managed jobs client SDK queue helpers."""
from unittest import mock

import pytest

from sky.jobs.client import sdk as jobs_sdk
from sky.jobs.client import sdk_async as jobs_sdk_async


def test_queue_version_2_dispatches_to_queue_v2():
    raw_queue = jobs_sdk.queue.__wrapped__.__wrapped__

    with mock.patch.object(jobs_sdk, 'queue_v2',
                           return_value='request-id-v2') as mock_queue_v2:
        result = raw_queue(refresh=True,
                           skip_finished=True,
                           all_users=True,
                           job_ids=[1, 2],
                           version=2)

    assert result == 'request-id-v2'
    mock_queue_v2.assert_called_once_with(refresh=True,
                                          skip_finished=True,
                                          all_users=True,
                                          job_ids=[1, 2])


def test_queue_version_1_warns_and_uses_legacy_endpoint():
    raw_queue = jobs_sdk.queue.__wrapped__.__wrapped__

    with mock.patch.object(jobs_sdk.server_common,
                           'make_authenticated_request',
                           return_value='response') as mock_request, \
         mock.patch.object(jobs_sdk.server_common,
                           'get_request_id',
                           return_value='request-id-v1') as mock_get_request_id, \
         mock.patch.object(jobs_sdk.logger, 'warning') as mock_warning:
        result = raw_queue(refresh=False,
                           skip_finished=True,
                           all_users=False,
                           job_ids=[3],
                           version=1)

    assert result == 'request-id-v1'
    mock_warning.assert_called_once()
    assert 'is deprecated and will be removed in v0.13' in mock_warning.call_args.args[
        0]
    mock_request.assert_called_once()
    args, kwargs = mock_request.call_args
    assert args == ('POST', '/jobs/queue')
    assert kwargs['json']['refresh'] is False
    assert kwargs['json']['skip_finished'] is True
    assert kwargs['json']['all_users'] is False
    assert kwargs['json']['job_ids'] == [3]
    mock_get_request_id.assert_called_once_with(response='response')


def test_queue_invalid_version_raises():
    raw_queue = jobs_sdk.queue.__wrapped__.__wrapped__

    with pytest.raises(ValueError, match='Must be 1 or 2'):
        raw_queue(refresh=False, version=3)


@pytest.mark.asyncio
async def test_async_queue_passes_version_through():

    async def mock_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    with mock.patch('sky.jobs.client.sdk_async.asyncio.to_thread',
                    side_effect=mock_to_thread), \
         mock.patch.object(jobs_sdk, 'queue',
                           return_value='request-id') as mock_queue, \
         mock.patch.object(jobs_sdk_async.sdk_async,
                           '_stream_and_get',
                           new=mock.AsyncMock(
                               return_value='queue-result')) as mock_stream:
        result = await jobs_sdk_async.queue(refresh=True,
                                            skip_finished=False,
                                            all_users=True,
                                            job_ids=[9],
                                            version=2)

    assert result == 'queue-result'
    mock_queue.assert_called_once_with(True, False, True, [9], 2)
    mock_stream.assert_called_once_with(
        'request-id', jobs_sdk_async.sdk_async.DEFAULT_STREAM_CONFIG)
