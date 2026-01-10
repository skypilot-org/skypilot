"""Unit tests for RequestQueue and scheduling in sky/server/requests/executor.py."""

import asyncio
import queue as queue_lib
import time
from unittest import mock

import pytest

from sky.server import config as server_config
from sky.server.requests import executor
from sky.server.requests import payloads
from sky.server.requests import preconditions
from sky.server.requests import requests as requests_lib


# Module-level function that can be pickled (unlike lambda)
def _dummy_picklable_entrypoint():
    """Dummy entrypoint function that can be pickled."""
    return None


class TestRequestQueue:
    """Tests for RequestQueue class."""

    def test_creates_with_multiprocessing_backend(self):
        """Test creating queue with multiprocessing backend."""
        with mock.patch(
                'sky.server.requests.queues.mp_queue.get_queue') as mock_get:
            mock_queue = mock.Mock()
            mock_get.return_value = mock_queue

            queue = executor.RequestQueue(
                requests_lib.ScheduleType.LONG,
                backend=server_config.QueueBackend.MULTIPROCESSING)

            mock_get.assert_called_once_with('long')
            assert queue.name == 'long'
            assert queue.backend == server_config.QueueBackend.MULTIPROCESSING

    def test_creates_with_local_backend(self):
        """Test creating queue with local backend."""
        with mock.patch(
                'sky.server.requests.queues.local_queue.get_queue') as mock_get:
            mock_queue = mock.Mock()
            mock_get.return_value = mock_queue

            queue = executor.RequestQueue(
                requests_lib.ScheduleType.SHORT,
                backend=server_config.QueueBackend.LOCAL)

            mock_get.assert_called_once_with('short')
            assert queue.name == 'short'
            assert queue.backend == server_config.QueueBackend.LOCAL

    def test_raises_on_invalid_backend(self):
        """Test that invalid backend raises RuntimeError."""
        with pytest.raises(RuntimeError, match='Invalid queue backend'):
            executor.RequestQueue(requests_lib.ScheduleType.LONG, backend=None)

    def test_put_adds_request_to_queue(self):
        """Test that put() adds request tuple to queue."""
        mock_queue = mock.Mock()

        with mock.patch('sky.server.requests.queues.local_queue.get_queue',
                        return_value=mock_queue):
            queue = executor.RequestQueue(
                requests_lib.ScheduleType.LONG,
                backend=server_config.QueueBackend.LOCAL)

            request_tuple = ('request-id', False, True)
            queue.put(request_tuple)

            mock_queue.put.assert_called_once_with(request_tuple)

    def test_get_returns_request_when_available(self):
        """Test that get() returns request when queue has items."""
        mock_queue = mock.Mock()
        request_tuple = ('request-id', True, False)
        mock_queue.get.return_value = request_tuple

        with mock.patch('sky.server.requests.queues.local_queue.get_queue',
                        return_value=mock_queue):
            queue = executor.RequestQueue(
                requests_lib.ScheduleType.SHORT,
                backend=server_config.QueueBackend.LOCAL)

            result = queue.get()

            assert result == request_tuple
            mock_queue.get.assert_called_once_with(block=False)

    def test_get_returns_none_when_empty(self):
        """Test that get() returns None when queue is empty."""
        mock_queue = mock.Mock()
        mock_queue.get.side_effect = queue_lib.Empty()

        with mock.patch('sky.server.requests.queues.local_queue.get_queue',
                        return_value=mock_queue):
            queue = executor.RequestQueue(
                requests_lib.ScheduleType.LONG,
                backend=server_config.QueueBackend.LOCAL)

            result = queue.get()

            assert result is None

    def test_len_returns_queue_size(self):
        """Test that __len__ returns queue size."""
        mock_queue = mock.Mock()
        mock_queue.qsize.return_value = 5

        with mock.patch('sky.server.requests.queues.local_queue.get_queue',
                        return_value=mock_queue):
            queue = executor.RequestQueue(
                requests_lib.ScheduleType.LONG,
                backend=server_config.QueueBackend.LOCAL)

            result = len(queue)

            assert result == 5


class TestGetQueue:
    """Tests for _get_queue() function."""

    def setup_method(self):
        """Clear cache before each test."""
        executor._get_queue.cache_clear()

    def teardown_method(self):
        """Clear cache after each test."""
        executor._get_queue.cache_clear()

    def test_returns_cached_queue(self):
        """Test that queue is cached and reused."""
        with mock.patch(
                'sky.server.requests.executor.RequestQueue') as mock_class:
            mock_queue = mock.Mock()
            mock_class.return_value = mock_queue

            # First call
            result1 = executor._get_queue(requests_lib.ScheduleType.LONG)

            # Second call should use cache
            result2 = executor._get_queue(requests_lib.ScheduleType.LONG)

            assert result1 == result2
            # Should only create one instance
            assert mock_class.call_count == 1


class TestRequestThreadExecutor:
    """Tests for get_request_thread_executor()."""

    def setup_method(self):
        """Reset the global executor."""
        executor._REQUEST_THREAD_EXECUTOR = None

    def teardown_method(self):
        """Clean up the executor."""
        if executor._REQUEST_THREAD_EXECUTOR is not None:
            executor._REQUEST_THREAD_EXECUTOR.shutdown(wait=False)
            executor._REQUEST_THREAD_EXECUTOR = None

    def test_lazy_initialization(self):
        """Test that executor is lazily initialized."""
        assert executor._REQUEST_THREAD_EXECUTOR is None

        result = executor.get_request_thread_executor()

        assert executor._REQUEST_THREAD_EXECUTOR is not None
        assert result == executor._REQUEST_THREAD_EXECUTOR

    def test_returns_same_executor(self):
        """Test that same executor is returned on subsequent calls."""
        result1 = executor.get_request_thread_executor()
        result2 = executor.get_request_thread_executor()

        assert result1 is result2


class TestSchedulePreparedRequest:
    """Tests for schedule_prepared_request()."""

    def test_enqueues_request_without_precondition(self):
        """Test that request is immediately enqueued without precondition."""
        mock_queue = mock.Mock()

        request = requests_lib.Request(
            request_id='test-request',
            name='test-name',
            entrypoint=lambda: None,
            request_body=payloads.RequestBody(),
            status=requests_lib.RequestStatus.PENDING,
            created_at=time.time(),
            schedule_type=requests_lib.ScheduleType.LONG,
            user_id='test-user')

        with mock.patch.object(executor, '_get_queue', return_value=mock_queue):
            executor.schedule_prepared_request(request,
                                               ignore_return_value=False,
                                               precondition=None,
                                               retryable=True)

            mock_queue.put.assert_called_once_with(
                ('test-request', False, True))

    def test_waits_for_precondition(self):
        """Test that request waits for precondition before enqueuing."""
        mock_queue = mock.Mock()
        mock_precondition = mock.Mock(spec=preconditions.Precondition)

        request = requests_lib.Request(
            request_id='test-request',
            name='test-name',
            entrypoint=lambda: None,
            request_body=payloads.RequestBody(),
            status=requests_lib.RequestStatus.PENDING,
            created_at=time.time(),
            schedule_type=requests_lib.ScheduleType.SHORT,
            user_id='test-user')

        with mock.patch.object(executor, '_get_queue', return_value=mock_queue):
            executor.schedule_prepared_request(request,
                                               ignore_return_value=True,
                                               precondition=mock_precondition,
                                               retryable=False)

            # Should call wait_async on precondition
            mock_precondition.wait_async.assert_called_once()
            # Queue should not be called yet (waiting for precondition)
            mock_queue.put.assert_not_called()


class TestRequestWorker:
    """Tests for RequestWorker class."""

    def test_creates_with_config(self):
        """Test creating worker with configuration."""
        config = server_config.WorkerConfig(garanteed_parallelism=4,
                                            burstable_parallelism=2,
                                            num_db_connections_per_worker=5)

        worker = executor.RequestWorker(
            schedule_type=requests_lib.ScheduleType.LONG, config=config)

        assert worker.schedule_type == requests_lib.ScheduleType.LONG
        assert worker.garanteed_parallelism == 4
        assert worker.burstable_parallelism == 2
        assert worker.num_db_connections_per_worker == 5

    def test_str_representation(self):
        """Test string representation of worker."""
        config = server_config.WorkerConfig(garanteed_parallelism=2,
                                            burstable_parallelism=1,
                                            num_db_connections_per_worker=0)

        worker = executor.RequestWorker(
            schedule_type=requests_lib.ScheduleType.SHORT, config=config)

        assert 'short' in str(worker)
        assert 'Worker' in str(worker)


class TestCoroutineTask:
    """Tests for CoroutineTask class."""

    @pytest.mark.asyncio
    async def test_wraps_asyncio_task(self):
        """Test that CoroutineTask wraps asyncio.Task."""

        async def dummy_coro():
            await asyncio.sleep(0.1)
            return 'done'

        task = asyncio.create_task(dummy_coro())
        coroutine_task = executor.CoroutineTask(task)

        assert coroutine_task.task is task

    @pytest.mark.asyncio
    async def test_cancel_stops_task(self):
        """Test that cancel() stops the underlying task."""

        async def long_running_coro():
            await asyncio.sleep(10)
            return 'done'

        task = asyncio.create_task(long_running_coro())
        coroutine_task = executor.CoroutineTask(task)

        await coroutine_task.cancel()

        assert task.cancelled()


class TestCheckRequestThreadExecutorAvailable:
    """Tests for check_request_thread_executor_available()."""

    def setup_method(self):
        executor._REQUEST_THREAD_EXECUTOR = None

    def teardown_method(self):
        if executor._REQUEST_THREAD_EXECUTOR is not None:
            executor._REQUEST_THREAD_EXECUTOR.shutdown(wait=False)
            executor._REQUEST_THREAD_EXECUTOR = None

    def test_creates_executor_if_not_exists(self):
        """Test that executor is created if it doesn't exist."""
        assert executor._REQUEST_THREAD_EXECUTOR is None

        # This should create the executor
        with mock.patch.object(executor.threads.OnDemandThreadExecutor,
                               'check_available') as mock_check:
            executor.check_request_thread_executor_available()

            mock_check.assert_called_once()


class TestPrepareRequestAsync:
    """Tests for prepare_request_async()."""

    @pytest.fixture()
    def isolated_database(self, tmp_path):
        """Create an isolated DB and logs directory per-test."""
        temp_db_path = tmp_path / 'requests.db'
        temp_log_path = tmp_path / 'logs'
        temp_log_path.mkdir()

        from sky.server import constants as server_constants

        with mock.patch.object(server_constants, 'API_SERVER_REQUEST_DB_PATH',
                               str(temp_db_path)):
            with mock.patch(
                    'sky.server.requests.requests.REQUEST_LOG_PATH_PREFIX',
                    str(temp_log_path)):
                requests_lib._DB = None
                yield
                requests_lib._DB = None

    @pytest.mark.asyncio
    async def test_creates_request(self, isolated_database):
        """Test that prepare_request_async creates a request."""
        from sky.skylet import constants

        request_body = payloads.RequestBody(
            env_vars={
                constants.USER_ID_ENV_VAR: 'test-user',
                constants.USER_ENV_VAR: 'test-user'
            })

        with mock.patch('sky.global_user_state.add_or_update_user'):
            request = await executor.prepare_request_async(
                request_id='test-id',
                request_name='test.request',
                request_body=request_body,
                func=_dummy_picklable_entrypoint,
                schedule_type=requests_lib.ScheduleType.SHORT)

            assert request.request_id == 'test-id'
            assert request.status == requests_lib.RequestStatus.PENDING

    @pytest.mark.asyncio
    async def test_raises_on_duplicate_request(self, isolated_database):
        """Test that duplicate request ID raises error."""
        from sky import exceptions
        from sky.skylet import constants

        request_body = payloads.RequestBody(
            env_vars={
                constants.USER_ID_ENV_VAR: 'test-user',
                constants.USER_ENV_VAR: 'test-user'
            })

        with mock.patch('sky.global_user_state.add_or_update_user'):
            # First creation should succeed
            await executor.prepare_request_async(
                request_id='duplicate-id',
                request_name='test.request',
                request_body=request_body,
                func=_dummy_picklable_entrypoint)

            # Second creation should raise
            with pytest.raises(exceptions.RequestAlreadyExistsError):
                await executor.prepare_request_async(
                    request_id='duplicate-id',
                    request_name='test.request',
                    request_body=request_body,
                    func=_dummy_picklable_entrypoint)
