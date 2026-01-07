"""Unit tests for sky.jobs.recovery_strategy module.

This module tests the recovery strategy logic for managed jobs, including:
- StrategyExecutor initialization and factory methods
- should_restart_on_failure logic with exit codes
- FailoverStrategyExecutor and EagerFailoverStrategyExecutor strategies
"""

import asyncio
from typing import List, Optional, Set
from unittest import mock

import pytest

from sky import backends
from sky import dag as dag_lib
from sky import resources as resources_lib
from sky import task as task_lib
from sky.jobs import recovery_strategy


class MockBackend(backends.CloudVmRayBackend):
    """Mock backend for testing."""

    def __init__(self):
        # Don't call super().__init__() to avoid initialization side effects
        self.run_timestamp = 'mock-timestamp'


class TestStrategyExecutorInit:
    """Tests for StrategyExecutor initialization."""

    @pytest.fixture
    def mock_backend(self):
        """Create a mock backend."""
        with mock.patch.object(backends.CloudVmRayBackend,
                               '__init__',
                               return_value=None):
            backend = backends.CloudVmRayBackend()
            backend.run_timestamp = 'mock-timestamp'
            return backend

    @pytest.fixture
    def mock_task(self):
        """Create a mock task with resources."""
        task = task_lib.Task('test-task')
        # Create resources without job_recovery to avoid validation issues
        resources = resources_lib.Resources()
        task.set_resources({resources})
        return task

    @pytest.fixture
    def async_primitives(self):
        """Create async primitives for testing."""
        starting: Set[int] = set()
        starting_lock = asyncio.Lock()
        starting_signal = asyncio.Condition(lock=starting_lock)
        return starting, starting_lock, starting_signal

    def test_init_basic(self, mock_backend, mock_task, async_primitives):
        """Test basic StrategyExecutor initialization."""
        starting, starting_lock, starting_signal = async_primitives

        executor = recovery_strategy.StrategyExecutor(
            cluster_name='test-cluster',
            backend=mock_backend,
            task=mock_task,
            max_restarts_on_errors=3,
            job_id=1,
            task_id=0,
            pool=None,
            starting=starting,
            starting_lock=starting_lock,
            starting_signal=starting_signal,
            recover_on_exit_codes=[1, 2, 3],
        )

        assert executor.cluster_name == 'test-cluster'
        assert executor.max_restarts_on_errors == 3
        assert executor.job_id == 1
        assert executor.task_id == 0
        assert executor.pool is None
        assert executor.recover_on_exit_codes == [1, 2, 3]
        assert executor.restart_cnt_on_failure == 0

    def test_init_with_pool(self, mock_backend, mock_task, async_primitives):
        """Test StrategyExecutor initialization with pool."""
        starting, starting_lock, starting_signal = async_primitives

        executor = recovery_strategy.StrategyExecutor(
            cluster_name=None,  # Pool jobs start without cluster name
            backend=mock_backend,
            task=mock_task,
            max_restarts_on_errors=0,
            job_id=2,
            task_id=0,
            pool='my-pool',
            starting=starting,
            starting_lock=starting_lock,
            starting_signal=starting_signal,
        )

        assert executor.cluster_name is None
        assert executor.pool == 'my-pool'

    def test_init_empty_recover_on_exit_codes(self, mock_backend, mock_task,
                                              async_primitives):
        """Test that None recover_on_exit_codes becomes empty list."""
        starting, starting_lock, starting_signal = async_primitives

        executor = recovery_strategy.StrategyExecutor(
            cluster_name='test-cluster',
            backend=mock_backend,
            task=mock_task,
            max_restarts_on_errors=0,
            job_id=1,
            task_id=0,
            pool=None,
            starting=starting,
            starting_lock=starting_lock,
            starting_signal=starting_signal,
            recover_on_exit_codes=None,
        )

        assert executor.recover_on_exit_codes == []


class TestShouldRestartOnFailure:
    """Tests for StrategyExecutor.should_restart_on_failure method."""

    @pytest.fixture
    def mock_backend(self):
        """Create a mock backend."""
        with mock.patch.object(backends.CloudVmRayBackend,
                               '__init__',
                               return_value=None):
            backend = backends.CloudVmRayBackend()
            backend.run_timestamp = 'mock-timestamp'
            return backend

    @pytest.fixture
    def mock_task(self):
        """Create a mock task."""
        task = task_lib.Task('test-task')
        resources = resources_lib.Resources()
        task.set_resources({resources})
        return task

    @pytest.fixture
    def async_primitives(self):
        """Create async primitives for testing."""
        starting: Set[int] = set()
        starting_lock = asyncio.Lock()
        starting_signal = asyncio.Condition(lock=starting_lock)
        return starting, starting_lock, starting_signal

    def _create_executor(self,
                         mock_backend,
                         mock_task,
                         async_primitives,
                         max_restarts: int = 3,
                         recover_on_exit_codes: Optional[List[int]] = None):
        """Helper to create executor with specific config."""
        starting, starting_lock, starting_signal = async_primitives
        return recovery_strategy.StrategyExecutor(
            cluster_name='test-cluster',
            backend=mock_backend,
            task=mock_task,
            max_restarts_on_errors=max_restarts,
            job_id=1,
            task_id=0,
            pool=None,
            starting=starting,
            starting_lock=starting_lock,
            starting_signal=starting_signal,
            recover_on_exit_codes=recover_on_exit_codes,
        )

    def test_restart_within_limit(self, mock_backend, mock_task,
                                  async_primitives):
        """Test restart is allowed within max_restarts_on_errors limit."""
        executor = self._create_executor(mock_backend,
                                         mock_task,
                                         async_primitives,
                                         max_restarts=3)

        # First three restarts should be allowed
        assert executor.should_restart_on_failure() is True
        assert executor.restart_cnt_on_failure == 1

        assert executor.should_restart_on_failure() is True
        assert executor.restart_cnt_on_failure == 2

        assert executor.should_restart_on_failure() is True
        assert executor.restart_cnt_on_failure == 3

    def test_restart_exceeds_limit(self, mock_backend, mock_task,
                                   async_primitives):
        """Test restart is denied when exceeding max_restarts_on_errors."""
        executor = self._create_executor(mock_backend,
                                         mock_task,
                                         async_primitives,
                                         max_restarts=2)

        assert executor.should_restart_on_failure() is True
        assert executor.should_restart_on_failure() is True
        # Third restart should be denied (exceeds limit of 2)
        assert executor.should_restart_on_failure() is False
        assert executor.restart_cnt_on_failure == 3

    def test_restart_zero_limit(self, mock_backend, mock_task,
                                async_primitives):
        """Test no restarts allowed when max_restarts_on_errors is 0."""
        executor = self._create_executor(mock_backend,
                                         mock_task,
                                         async_primitives,
                                         max_restarts=0)

        assert executor.should_restart_on_failure() is False
        assert executor.restart_cnt_on_failure == 1

    def test_exit_code_triggers_recovery(self, mock_backend, mock_task,
                                         async_primitives):
        """Test matching exit code triggers recovery without counter increment."""
        executor = self._create_executor(mock_backend,
                                         mock_task,
                                         async_primitives,
                                         max_restarts=0,
                                         recover_on_exit_codes=[42, 137])

        # Even with max_restarts=0, matching exit code should trigger recovery
        assert executor.should_restart_on_failure(exit_codes=[42]) is True
        # Counter should NOT be incremented for exit code matches
        assert executor.restart_cnt_on_failure == 0

    def test_exit_code_match_multiple_codes(self, mock_backend, mock_task,
                                            async_primitives):
        """Test recovery when one of multiple exit codes matches."""
        executor = self._create_executor(mock_backend,
                                         mock_task,
                                         async_primitives,
                                         max_restarts=0,
                                         recover_on_exit_codes=[1, 2, 137])

        # Should match on 137 even when other codes are present
        assert executor.should_restart_on_failure(exit_codes=[0, 137]) is True
        assert executor.restart_cnt_on_failure == 0

    def test_exit_code_no_match_falls_back_to_counter(self, mock_backend,
                                                      mock_task,
                                                      async_primitives):
        """Test non-matching exit code falls back to counter logic."""
        executor = self._create_executor(mock_backend,
                                         mock_task,
                                         async_primitives,
                                         max_restarts=1,
                                         recover_on_exit_codes=[42])

        # Exit code 1 doesn't match, so counter logic applies
        assert executor.should_restart_on_failure(exit_codes=[1]) is True
        assert executor.restart_cnt_on_failure == 1

        # Second call should fail (counter exceeded)
        assert executor.should_restart_on_failure(exit_codes=[1]) is False

    def test_empty_exit_codes_uses_counter(self, mock_backend, mock_task,
                                           async_primitives):
        """Test empty exit codes list uses counter logic."""
        executor = self._create_executor(mock_backend,
                                         mock_task,
                                         async_primitives,
                                         max_restarts=1,
                                         recover_on_exit_codes=[42])

        assert executor.should_restart_on_failure(exit_codes=[]) is True
        assert executor.restart_cnt_on_failure == 1

    def test_none_exit_codes_uses_counter(self, mock_backend, mock_task,
                                          async_primitives):
        """Test None exit codes uses counter logic."""
        executor = self._create_executor(mock_backend,
                                         mock_task,
                                         async_primitives,
                                         max_restarts=1,
                                         recover_on_exit_codes=[42])

        assert executor.should_restart_on_failure(exit_codes=None) is True
        assert executor.restart_cnt_on_failure == 1


class TestStrategyExecutorMake:
    """Tests for StrategyExecutor.make factory method."""

    @pytest.fixture
    def mock_backend(self):
        """Create a mock backend."""
        with mock.patch.object(backends.CloudVmRayBackend,
                               '__init__',
                               return_value=None):
            backend = backends.CloudVmRayBackend()
            backend.run_timestamp = 'mock-timestamp'
            return backend

    @pytest.fixture
    def async_primitives(self):
        """Create async primitives for testing."""
        starting: Set[int] = set()
        starting_lock = asyncio.Lock()
        starting_signal = asyncio.Condition(lock=starting_lock)
        return starting, starting_lock, starting_signal

    def test_make_default_strategy(self, mock_backend, async_primitives):
        """Test make returns default EAGER_NEXT_REGION strategy via dict config."""
        task = task_lib.Task('test-task')
        # Explicitly use EAGER_NEXT_REGION (the default strategy)
        resources = resources_lib.Resources(
            job_recovery={'strategy': 'EAGER_NEXT_REGION'})
        task.set_resources({resources})

        starting, starting_lock, starting_signal = async_primitives

        executor = recovery_strategy.StrategyExecutor.make(
            cluster_name='test-cluster',
            backend=mock_backend,
            task=task,
            job_id=1,
            task_id=0,
            pool=None,
            starting=starting,
            starting_lock=starting_lock,
            starting_signal=starting_signal,
        )

        # Default strategy should be EAGER_NEXT_REGION
        assert isinstance(executor,
                          recovery_strategy.EagerFailoverStrategyExecutor)

    def test_make_failover_strategy(self, mock_backend, async_primitives):
        """Test make returns FAILOVER strategy when specified."""
        task = task_lib.Task('test-task')
        resources = resources_lib.Resources(job_recovery='FAILOVER')
        task.set_resources({resources})

        starting, starting_lock, starting_signal = async_primitives

        executor = recovery_strategy.StrategyExecutor.make(
            cluster_name='test-cluster',
            backend=mock_backend,
            task=task,
            job_id=1,
            task_id=0,
            pool=None,
            starting=starting,
            starting_lock=starting_lock,
            starting_signal=starting_signal,
        )

        assert isinstance(executor, recovery_strategy.FailoverStrategyExecutor)
        # EagerFailoverStrategyExecutor is a subclass, so check it's not that
        assert type(executor) is recovery_strategy.FailoverStrategyExecutor

    def test_make_eager_next_region_strategy(self, mock_backend,
                                             async_primitives):
        """Test make returns EAGER_NEXT_REGION strategy when specified."""
        task = task_lib.Task('test-task')
        resources = resources_lib.Resources(job_recovery='EAGER_NEXT_REGION')
        task.set_resources({resources})

        starting, starting_lock, starting_signal = async_primitives

        executor = recovery_strategy.StrategyExecutor.make(
            cluster_name='test-cluster',
            backend=mock_backend,
            task=task,
            job_id=1,
            task_id=0,
            pool=None,
            starting=starting,
            starting_lock=starting_lock,
            starting_signal=starting_signal,
        )

        assert isinstance(executor,
                          recovery_strategy.EagerFailoverStrategyExecutor)

    def test_make_with_dict_config(self, mock_backend, async_primitives):
        """Test make with dictionary job_recovery config."""
        task = task_lib.Task('test-task')
        resources = resources_lib.Resources(
            job_recovery={
                'strategy': 'FAILOVER',
                'max_restarts_on_errors': 5,
                'recover_on_exit_codes': [42, 137],
            })
        task.set_resources({resources})

        starting, starting_lock, starting_signal = async_primitives

        executor = recovery_strategy.StrategyExecutor.make(
            cluster_name='test-cluster',
            backend=mock_backend,
            task=task,
            job_id=1,
            task_id=0,
            pool=None,
            starting=starting,
            starting_lock=starting_lock,
            starting_signal=starting_signal,
        )

        assert type(executor) is recovery_strategy.FailoverStrategyExecutor
        assert executor.max_restarts_on_errors == 5
        assert executor.recover_on_exit_codes == [42, 137]

    def test_make_with_single_exit_code(self, mock_backend, async_primitives):
        """Test make normalizes single integer exit code to list."""
        task = task_lib.Task('test-task')
        resources = resources_lib.Resources(
            job_recovery={
                'strategy': 'EAGER_NEXT_REGION',  # Must specify strategy
                'recover_on_exit_codes': 42,  # Single int, not list
            })
        task.set_resources({resources})

        starting, starting_lock, starting_signal = async_primitives

        executor = recovery_strategy.StrategyExecutor.make(
            cluster_name='test-cluster',
            backend=mock_backend,
            task=task,
            job_id=1,
            task_id=0,
            pool=None,
            starting=starting,
            starting_lock=starting_lock,
            starting_signal=starting_signal,
        )

        assert executor.recover_on_exit_codes == [42]

    def test_make_removes_job_recovery_from_resources(self, mock_backend,
                                                      async_primitives):
        """Test make removes job_recovery from task resources."""
        task = task_lib.Task('test-task')
        resources = resources_lib.Resources(job_recovery='FAILOVER')
        task.set_resources({resources})

        starting, starting_lock, starting_signal = async_primitives

        recovery_strategy.StrategyExecutor.make(
            cluster_name='test-cluster',
            backend=mock_backend,
            task=task,
            job_id=1,
            task_id=0,
            pool=None,
            starting=starting,
            starting_lock=starting_lock,
            starting_signal=starting_signal,
        )

        # After make(), resources should have job_recovery=None
        for r in task.resources:
            assert r.job_recovery is None

    def test_make_with_mismatched_strategies_raises(self, mock_backend,
                                                    async_primitives):
        """Test make raises when resources have different strategies."""
        task = task_lib.Task('test-task')
        resources1 = resources_lib.Resources(job_recovery='FAILOVER')
        resources2 = resources_lib.Resources(job_recovery='EAGER_NEXT_REGION')
        task.set_resources({resources1, resources2})

        starting, starting_lock, starting_signal = async_primitives

        with pytest.raises(ValueError, match='should be the same'):
            recovery_strategy.StrategyExecutor.make(
                cluster_name='test-cluster',
                backend=mock_backend,
                task=task,
                job_id=1,
                task_id=0,
                pool=None,
                starting=starting,
                starting_lock=starting_lock,
                starting_signal=starting_signal,
            )


class TestFailoverStrategyExecutor:
    """Tests for FailoverStrategyExecutor class."""

    @pytest.fixture
    def mock_backend(self):
        """Create a mock backend."""
        with mock.patch.object(backends.CloudVmRayBackend,
                               '__init__',
                               return_value=None):
            backend = backends.CloudVmRayBackend()
            backend.run_timestamp = 'mock-timestamp'
            return backend

    @pytest.fixture
    def mock_task(self):
        """Create a mock task."""
        task = task_lib.Task('test-task')
        resources = resources_lib.Resources()
        task.set_resources({resources})
        return task

    @pytest.fixture
    def async_primitives(self):
        """Create async primitives for testing."""
        starting: Set[int] = set()
        starting_lock = asyncio.Lock()
        starting_signal = asyncio.Condition(lock=starting_lock)
        return starting, starting_lock, starting_signal

    def test_failover_init(self, mock_backend, mock_task, async_primitives):
        """Test FailoverStrategyExecutor initialization."""
        starting, starting_lock, starting_signal = async_primitives

        executor = recovery_strategy.FailoverStrategyExecutor(
            cluster_name='test-cluster',
            backend=mock_backend,
            task=mock_task,
            max_restarts_on_errors=0,
            job_id=1,
            task_id=0,
            pool=None,
            starting=starting,
            starting_lock=starting_lock,
            starting_signal=starting_signal,
        )

        assert executor._launched_resources is None
        assert executor._MAX_RETRY_CNT == 240

    def test_failover_inherits_from_strategy_executor(self, mock_backend,
                                                      mock_task,
                                                      async_primitives):
        """Test FailoverStrategyExecutor inherits from StrategyExecutor."""
        starting, starting_lock, starting_signal = async_primitives

        executor = recovery_strategy.FailoverStrategyExecutor(
            cluster_name='test-cluster',
            backend=mock_backend,
            task=mock_task,
            max_restarts_on_errors=5,
            job_id=1,
            task_id=0,
            pool=None,
            starting=starting,
            starting_lock=starting_lock,
            starting_signal=starting_signal,
        )

        assert isinstance(executor, recovery_strategy.StrategyExecutor)
        assert executor.max_restarts_on_errors == 5


class TestEagerFailoverStrategyExecutor:
    """Tests for EagerFailoverStrategyExecutor class."""

    @pytest.fixture
    def mock_backend(self):
        """Create a mock backend."""
        with mock.patch.object(backends.CloudVmRayBackend,
                               '__init__',
                               return_value=None):
            backend = backends.CloudVmRayBackend()
            backend.run_timestamp = 'mock-timestamp'
            return backend

    @pytest.fixture
    def mock_task(self):
        """Create a mock task."""
        task = task_lib.Task('test-task')
        resources = resources_lib.Resources()
        task.set_resources({resources})
        return task

    @pytest.fixture
    def async_primitives(self):
        """Create async primitives for testing."""
        starting: Set[int] = set()
        starting_lock = asyncio.Lock()
        starting_signal = asyncio.Condition(lock=starting_lock)
        return starting, starting_lock, starting_signal

    def test_eager_failover_inherits_from_failover(self, mock_backend,
                                                   mock_task, async_primitives):
        """Test EagerFailoverStrategyExecutor inherits from FailoverStrategyExecutor."""
        starting, starting_lock, starting_signal = async_primitives

        executor = recovery_strategy.EagerFailoverStrategyExecutor(
            cluster_name='test-cluster',
            backend=mock_backend,
            task=mock_task,
            max_restarts_on_errors=0,
            job_id=1,
            task_id=0,
            pool=None,
            starting=starting,
            starting_lock=starting_lock,
            starting_signal=starting_signal,
        )

        assert isinstance(executor, recovery_strategy.FailoverStrategyExecutor)
        assert isinstance(executor, recovery_strategy.StrategyExecutor)


class TestCleanupCluster:
    """Tests for _cleanup_cluster method."""

    @pytest.fixture
    def mock_backend(self):
        """Create a mock backend."""
        with mock.patch.object(backends.CloudVmRayBackend,
                               '__init__',
                               return_value=None):
            backend = backends.CloudVmRayBackend()
            backend.run_timestamp = 'mock-timestamp'
            return backend

    @pytest.fixture
    def mock_task(self):
        """Create a mock task."""
        task = task_lib.Task('test-task')
        resources = resources_lib.Resources()
        task.set_resources({resources})
        return task

    @pytest.fixture
    def async_primitives(self):
        """Create async primitives for testing."""
        starting: Set[int] = set()
        starting_lock = asyncio.Lock()
        starting_signal = asyncio.Condition(lock=starting_lock)
        return starting, starting_lock, starting_signal

    def test_cleanup_with_none_cluster_name(self, mock_backend, mock_task,
                                            async_primitives):
        """Test _cleanup_cluster does nothing when cluster_name is None."""
        starting, starting_lock, starting_signal = async_primitives

        executor = recovery_strategy.StrategyExecutor(
            cluster_name=None,
            backend=mock_backend,
            task=mock_task,
            max_restarts_on_errors=0,
            job_id=1,
            task_id=0,
            pool=None,
            starting=starting,
            starting_lock=starting_lock,
            starting_signal=starting_signal,
        )

        with mock.patch('sky.jobs.utils.terminate_cluster') as mock_terminate:
            executor._cleanup_cluster()
            mock_terminate.assert_not_called()

    def test_cleanup_with_pool(self, mock_backend, mock_task, async_primitives):
        """Test _cleanup_cluster does nothing when using a pool."""
        starting, starting_lock, starting_signal = async_primitives

        executor = recovery_strategy.StrategyExecutor(
            cluster_name='test-cluster',
            backend=mock_backend,
            task=mock_task,
            max_restarts_on_errors=0,
            job_id=1,
            task_id=0,
            pool='my-pool',
            starting=starting,
            starting_lock=starting_lock,
            starting_signal=starting_signal,
        )

        with mock.patch('sky.jobs.utils.terminate_cluster') as mock_terminate:
            executor._cleanup_cluster()
            mock_terminate.assert_not_called()

    def test_cleanup_calls_terminate(self, mock_backend, mock_task,
                                     async_primitives):
        """Test _cleanup_cluster calls terminate_cluster."""
        starting, starting_lock, starting_signal = async_primitives

        executor = recovery_strategy.StrategyExecutor(
            cluster_name='test-cluster',
            backend=mock_backend,
            task=mock_task,
            max_restarts_on_errors=0,
            job_id=1,
            task_id=0,
            pool=None,
            starting=starting,
            starting_lock=starting_lock,
            starting_signal=starting_signal,
        )

        with mock.patch('sky.jobs.utils.terminate_cluster') as mock_terminate:
            executor._cleanup_cluster()
            mock_terminate.assert_called_once_with('test-cluster')


class TestGetLoggerFile:
    """Tests for _get_logger_file function."""

    def test_get_logger_file_with_file_handler(self):
        """Test _get_logger_file returns path when FileHandler exists."""
        import logging
        import tempfile

        # Create a logger with a file handler
        logger = logging.getLogger('test_logger')
        logger.handlers.clear()  # Clear any existing handlers

        with tempfile.NamedTemporaryFile(suffix='.log', delete=False) as f:
            handler = logging.FileHandler(f.name)
            logger.addHandler(handler)

            result = recovery_strategy._get_logger_file(logger)
            assert result == f.name

            # Cleanup
            handler.close()
            logger.removeHandler(handler)

    def test_get_logger_file_without_file_handler(self):
        """Test _get_logger_file returns None when no FileHandler."""
        import logging

        logger = logging.getLogger('test_logger_no_file')
        logger.handlers.clear()  # Clear any existing handlers

        # Add only a stream handler
        handler = logging.StreamHandler()
        logger.addHandler(handler)

        result = recovery_strategy._get_logger_file(logger)
        assert result is None

        # Cleanup
        logger.removeHandler(handler)

    def test_get_logger_file_with_empty_handlers(self):
        """Test _get_logger_file returns None with no handlers."""
        import logging

        logger = logging.getLogger('test_logger_empty')
        logger.handlers.clear()

        result = recovery_strategy._get_logger_file(logger)
        assert result is None
