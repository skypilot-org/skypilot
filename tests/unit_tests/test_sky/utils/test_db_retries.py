"""Unit tests for sky.utils.db.retries."""
# pylint: disable=missing-class-docstring,protected-access,unnecessary-lambda
import socket
import time
from unittest import mock

import psycopg2
import pytest
import sqlalchemy.exc

from sky.utils.db import deadline as db_deadline
from sky.utils.db import retries


def _make_op_error(msg: str = 'boom') -> sqlalchemy.exc.OperationalError:
    return sqlalchemy.exc.OperationalError(statement='SELECT 1',
                                           params={},
                                           orig=Exception(msg))


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


def _deadline_error(reason='client_deadline'):
    return sqlalchemy.exc.OperationalError(
        'stmt', {}, db_deadline.DBDeadlineExceeded('x', reason=reason))


class _PgError(Exception):

    def __init__(self, pgcode):
        super().__init__('cancelled')
        self.pgcode = pgcode


class TestRetriesUnderAnAuthDeadline:
    """With a thread-local deadline set (the bounded auth path), retrying
    cannot help: a deadline error IS the bound firing, and a backoff sleep
    that outlives the deadline holds the thread the bound exists to free.
    Without a deadline, nothing changes."""

    @pytest.fixture(autouse=True)
    def _clear(self):
        db_deadline.clear_deadline()
        yield
        db_deadline.clear_deadline()

    def test_a_client_deadline_error_is_not_retried(self):
        db_deadline.set_deadline(time.monotonic() + 60)
        fn = mock.Mock(side_effect=_deadline_error())
        with mock.patch.object(retries.time, 'sleep') as sleep:
            with pytest.raises(sqlalchemy.exc.OperationalError):
                retries.with_db_retries(fn)
        fn.assert_called_once()
        sleep.assert_not_called()

    @pytest.mark.parametrize('pgcode', ['55P03', '57014', '25P03'])
    def test_a_server_timeout_is_not_retried(self, pgcode):
        db_deadline.set_deadline(time.monotonic() + 60)
        fn = mock.Mock(side_effect=sqlalchemy.exc.OperationalError(
            'stmt', {}, _PgError(pgcode)))
        with mock.patch.object(retries.time, 'sleep') as sleep:
            with pytest.raises(sqlalchemy.exc.OperationalError):
                retries.with_db_retries(fn)
        fn.assert_called_once()
        sleep.assert_not_called()

    def test_a_backoff_past_the_deadline_stops_retrying(self):
        # A non-deadline transient error (e.g. EBADF on a stolen fd) with
        # 0.3s of budget left: the first backoff (~1s) would outlive it.
        db_deadline.set_deadline(time.monotonic() + 0.3)
        fn = mock.Mock(side_effect=_make_op_error('EBADF'))
        with mock.patch.object(retries.time, 'sleep') as sleep:
            with pytest.raises(sqlalchemy.exc.OperationalError):
                retries.with_db_retries(fn)
        fn.assert_called_once()
        sleep.assert_not_called()

    def test_a_transient_error_within_budget_is_still_retried(self):
        db_deadline.set_deadline(time.monotonic() + 60)
        fn = mock.Mock(side_effect=[_make_op_error('EBADF'), 'ok'])
        with mock.patch.object(retries.time, 'sleep'):
            assert retries.with_db_retries(fn) == 'ok'
        assert fn.call_count == 2

    def test_without_a_deadline_a_deadline_error_is_retried_as_before(self):
        fn = mock.Mock(side_effect=[_deadline_error(), 'ok'])
        with mock.patch.object(retries.time, 'sleep'):
            assert retries.with_db_retries(fn) == 'ok'
        assert fn.call_count == 2

    @pytest.mark.asyncio
    async def test_async_variant_stops_too(self):
        db_deadline.set_deadline(time.monotonic() + 60)
        fn = mock.AsyncMock(side_effect=_deadline_error())
        with mock.patch.object(retries.asyncio, 'sleep') as sleep:
            with pytest.raises(sqlalchemy.exc.OperationalError):
                await retries.with_db_retries_async(fn)
        fn.assert_called_once()
        sleep.assert_not_called()
