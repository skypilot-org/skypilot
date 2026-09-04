"""Unit tests for sky.utils.db.retries."""
# pylint: disable=missing-class-docstring,protected-access,unnecessary-lambda
import socket
from typing import Optional
from unittest import mock

import asyncpg
import psycopg2
import pytest
import sqlalchemy.exc

from sky.utils.db import retries


def _make_op_error(msg: str = 'boom') -> sqlalchemy.exc.OperationalError:
    return sqlalchemy.exc.OperationalError(statement='SELECT 1',
                                           params={},
                                           orig=Exception(msg))


def _wrapped(cause: BaseException,
             outer: Optional[BaseException] = None) -> BaseException:
    """`outer` (default RuntimeError) raised `from cause`."""
    err = outer if outer is not None else RuntimeError('wrapped')
    err.__cause__ = cause
    return err


class TestWithDbRetries:

    def test_returns_immediately_on_success(self):
        fn = mock.Mock(return_value='ok')
        with mock.patch.object(retries.time, 'sleep') as sleep:
            assert retries.with_db_retries(fn) == 'ok'
        fn.assert_called_once()
        sleep.assert_not_called()

    @pytest.mark.parametrize('exc_factory', [
        lambda: _make_op_error(),
        lambda: sqlalchemy.exc.InterfaceError('s', {}, Exception('x')),
        lambda: ConnectionError('unexpected connection_lost() call'),
        lambda: psycopg2.OperationalError('server closed the connection'),
        lambda: psycopg2.InterfaceError('connection already closed'),
        lambda: socket.gaierror(8, 'nodename nor servname provided'),
        lambda: asyncpg.exceptions.ProtocolViolationError(
            'database "d" is disabled'),
        lambda: asyncpg.exceptions.CannotConnectNowError(
            'the database system is starting up'),
        lambda: asyncpg.exceptions.TooManyConnectionsError(
            'too many connections for role "r"'),
        lambda: _wrapped(_make_op_error()),
    ])
    def test_retries_on_each_retryable_exception(self, exc_factory):
        # Fail twice, then succeed.
        fn = mock.Mock(side_effect=[exc_factory(), exc_factory(), 'ok'])
        with mock.patch.object(retries.time, 'sleep'):
            assert retries.with_db_retries(fn) == 'ok'
        assert fn.call_count == 3

    def test_does_not_retry_non_retryable(self):
        fn = mock.Mock(side_effect=ValueError('not retryable'))
        with mock.patch.object(retries.time, 'sleep') as sleep:
            with pytest.raises(ValueError, match='not retryable'):
                retries.with_db_retries(fn)
        fn.assert_called_once()
        sleep.assert_not_called()

    def test_integrity_error_is_not_retried(self):
        # IntegrityError is a DBAPIError but signals a programming/constraint
        # bug — retrying would mask it and burn time. Make sure we don't.
        fn = mock.Mock(side_effect=sqlalchemy.exc.IntegrityError(
            's', {}, Exception('duplicate key')))
        with mock.patch.object(retries.time, 'sleep'):
            with pytest.raises(sqlalchemy.exc.IntegrityError):
                retries.with_db_retries(fn)
        fn.assert_called_once()

    def test_raises_original_after_exhausting(self):
        op_err = _make_op_error('persistent')
        fn = mock.Mock(side_effect=op_err)
        with mock.patch.object(retries.time, 'sleep'):
            with pytest.raises(sqlalchemy.exc.OperationalError):
                retries.with_db_retries(fn, max_retries=3)
        assert fn.call_count == 3

    @pytest.mark.parametrize('bad_max_retries', [0, -1, -100])
    def test_invalid_max_retries_raises_value_error(self, bad_max_retries):
        fn = mock.Mock()
        with pytest.raises(ValueError, match='max_retries must be greater'):
            retries.with_db_retries(fn, max_retries=bad_max_retries)
        fn.assert_not_called()

    def test_logs_warning_on_each_retry(self):
        # SkyPilot disables logger propagation (sky_logging.py), so caplog
        # doesn't see records. Patch the logger directly instead.
        fn = mock.Mock(side_effect=[_make_op_error(), 'ok'])
        with mock.patch.object(retries.time, 'sleep'), \
             mock.patch.object(retries.logger, 'warning') as warn:
            retries.with_db_retries(fn)
        assert warn.call_count == 1
        msg = warn.call_args.args[0]
        assert 'Transient DB error' in msg
        assert 'attempt 1' in msg

    def test_logs_info_on_recovery(self):
        fn = mock.Mock(side_effect=[_make_op_error(), _make_op_error(), 'ok'])
        with mock.patch.object(retries.time, 'sleep'), \
             mock.patch.object(retries.logger, 'info') as info:
            retries.with_db_retries(fn)
        assert any('recovered after 2 retries' in c.args[0]
                   for c in info.call_args_list)

    def test_logs_error_on_exhaustion(self):
        fn = mock.Mock(side_effect=_make_op_error('persistent'))
        with mock.patch.object(retries.time, 'sleep'), \
             mock.patch.object(retries.logger, 'error') as err:
            with pytest.raises(sqlalchemy.exc.OperationalError):
                retries.with_db_retries(fn, max_retries=3)
        assert any('giving up after 3 attempts' in c.args[0]
                   for c in err.call_args_list)

    def test_sleeps_between_attempts(self):
        fn = mock.Mock(side_effect=[_make_op_error(), _make_op_error(), 'ok'])
        with mock.patch.object(retries.time, 'sleep') as sleep:
            retries.with_db_retries(fn)
        # 3 attempts → 2 sleeps in between.
        assert sleep.call_count == 2
        # Both delays should be positive numbers.
        for call in sleep.call_args_list:
            assert call.args[0] > 0


class TestWithDbRetriesAsync:

    @pytest.mark.asyncio
    async def test_returns_immediately_on_success(self):
        coro_fn = mock.Mock(return_value=_coro_returning('ok'))
        with mock.patch.object(retries.asyncio,
                               'sleep',
                               new_callable=mock.AsyncMock) as sleep:
            assert await retries.with_db_retries_async(coro_fn) == 'ok'
        sleep.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize('exc_factory', [
        lambda: _make_op_error(),
        lambda: ConnectionError('unexpected connection_lost() call'),
        lambda: socket.gaierror(8, 'nodename nor servname provided'),
        lambda: psycopg2.OperationalError('server closed the connection'),
        lambda: asyncpg.exceptions.ProtocolViolationError(
            'database "d" is disabled'),
    ])
    async def test_retries_on_each_retryable_exception(self, exc_factory):
        results = [
            _coro_raising(exc_factory()),
            _coro_raising(exc_factory()),
            _coro_returning('ok'),
        ]
        coro_fn = mock.Mock(side_effect=results)
        with mock.patch.object(retries.asyncio,
                               'sleep',
                               new_callable=mock.AsyncMock):
            assert await retries.with_db_retries_async(coro_fn) == 'ok'
        assert coro_fn.call_count == 3

    @pytest.mark.asyncio
    async def test_raises_after_exhausting(self):
        coro_fn = mock.Mock(
            side_effect=lambda _attempt: _coro_raising(_make_op_error()))
        with mock.patch.object(retries.asyncio,
                               'sleep',
                               new_callable=mock.AsyncMock):
            with pytest.raises(sqlalchemy.exc.OperationalError):
                await retries.with_db_retries_async(coro_fn, max_retries=3)
        assert coro_fn.call_count == 3

    @pytest.mark.asyncio
    @pytest.mark.parametrize('bad_max_retries', [0, -1, -100])
    async def test_invalid_max_retries_raises_value_error(
            self, bad_max_retries):
        coro_fn = mock.Mock()
        with pytest.raises(ValueError, match='max_retries must be greater'):
            await retries.with_db_retries_async(coro_fn,
                                                max_retries=bad_max_retries)
        coro_fn.assert_not_called()


class TestSummarize:

    def test_first_line_only_for_multiline(self):
        e = _make_op_error('first line\n\tsecond line\n\tthird line')
        summary = retries.summarize(e)
        assert '\n' not in summary
        assert 'OperationalError' in summary
        assert 'first line' in summary
        assert 'second line' not in summary

    def test_includes_exception_class_name(self):
        assert 'ConnectionError' in retries.summarize(ConnectionError('boom'))
        assert 'OperationalError' in retries.summarize(
            psycopg2.OperationalError('boom'))


async def _coro_returning(value):
    return value


async def _coro_raising(exc):
    raise exc


class TestIsTransient:

    def test_direct_retryable_exception(self):
        assert retries.is_transient(_make_op_error())

    def test_non_db_exception(self):
        assert not retries.is_transient(RuntimeError('boom'))

    def test_follows_cause_chain(self):
        assert retries.is_transient(_wrapped(_make_op_error()))

    def test_sqlalchemy_wrapper_around_asyncpg_error(self):
        # The asyncpg dialect maps PostgresError to the generic DBAPIError;
        # the asyncpg class survives only two levels down the cause chain.
        adapter_err = _wrapped(
            asyncpg.exceptions.ProtocolViolationError('query_wait_timeout'),
            Exception('adapter'))
        err = sqlalchemy.exc.DBAPIError('SELECT 1', {}, adapter_err)
        err.__cause__ = adapter_err
        assert retries.is_transient(err)

    def test_bare_network_error_counts_only_at_top_level(self):
        assert retries.is_transient(ConnectionResetError('reset'))
        assert not retries.is_transient(_wrapped(ConnectionResetError('reset')))


class TestDeadlineBudget:

    def test_deadline_bounds_the_loop(self):
        clock = [0.0]

        def fake_sleep(seconds):
            clock[0] += seconds

        fn = mock.Mock(side_effect=_make_op_error('persistent'))
        with mock.patch.object(retries.time, 'sleep', fake_sleep), \
             mock.patch.object(retries.time, 'monotonic', lambda: clock[0]):
            with pytest.raises(sqlalchemy.exc.OperationalError):
                retries.with_db_retries(fn,
                                        max_retries=None,
                                        initial_backoff=10,
                                        max_backoff=300,
                                        deadline=1000.0)
        assert fn.call_count > retries._DEFAULT_MAX_RETRIES
        # The last sleep is clamped to the remaining budget, so the final
        # attempt starts exactly at the deadline.
        assert clock[0] == pytest.approx(1000.0)

    @pytest.mark.parametrize('initial_backoff,max_backoff', [(0, 5.0),
                                                             (10.0, 5.0)])
    def test_invalid_backoff_bounds_raise_value_error(self, initial_backoff,
                                                      max_backoff):
        fn = mock.Mock()
        with pytest.raises(ValueError, match='initial_backoff'):
            retries.with_db_retries(fn,
                                    initial_backoff=initial_backoff,
                                    max_backoff=max_backoff)
        fn.assert_not_called()

    def test_attempt_bound_still_applies_with_deadline(self):
        fn = mock.Mock(side_effect=_make_op_error('persistent'))
        with mock.patch.object(retries.time, 'sleep'):
            with pytest.raises(sqlalchemy.exc.OperationalError):
                retries.with_db_retries(fn,
                                        max_retries=2,
                                        deadline=float('inf'))
        assert fn.call_count == 2

    def test_requires_a_bound(self):
        fn = mock.Mock()
        with pytest.raises(ValueError, match='max_retries or deadline'):
            retries.with_db_retries(fn, max_retries=None)
        fn.assert_not_called()
