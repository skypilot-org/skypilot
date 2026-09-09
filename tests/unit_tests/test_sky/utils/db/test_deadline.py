"""Unit tests for the auth-path DB deadline machinery.

These cover the parts that need no Postgres server:

* the wait callback is NOT installed as an import side effect (a plugin
  importing the auth modules in an executor process must not turn green mode
  on there);
* the wait callback frees a thread whose fd never becomes readable, at the
  deadline, and classifies a stolen / closed fd instead of handing it back to
  libpq (the blocking-socket freeze the design's earlier version had);
* the connect phase: ``connect_timeout`` is enforced (green mode otherwise
  ignores it) and the earlier of it and the thread deadline wins, a connect
  that gives up is closed at once (psycopg2 would otherwise leave that to
  deallocation, i.e. to the cyclic GC), and libpq swapping its socket during
  the handshake (address fallback, multi-host DSNs) is NOT reported as a
  stolen fd -- driven both through a scripted connection stand-in and through
  REAL psycopg2/libpq against local listeners that refuse, never answer, or
  hang up;
* the SET LOCAL listener: attached only to Postgres psycopg2 (sync) engines,
  emits the prefix on the first statement of a bounded transaction and
  nothing else -- keyed on the driver's own transaction state, so exactly
  once per transaction whatever other engine listeners run first -- resets
  per transaction, skips executemany / named cursors / autocommit;
* exception classification used by the caller and the retry logic.

The stolen/closed-fd and connect-phase tests with the stand-in drive the
callback directly with real sockets/pipes; the stand-in models psycopg2's
status transitions (an unexported CONNECTING value during the handshake) so
the predicate the callback keys on is the one real psycopg2 presents. The
listener tests run real SQLAlchemy machinery on a sqlite connection class
that presents psycopg2's ``status`` / ``autocommit`` surface.
"""
# pylint: disable=protected-access,missing-class-docstring,redefined-outer-name
import os
import re
import socket
import sqlite3
import threading
import time
from unittest import mock

import psycopg2
import pytest
import sqlalchemy
from sqlalchemy import orm

from sky.utils.db import deadline


@pytest.fixture(autouse=True)
def _clean_callback_and_deadline():
    """Never leak a wait callback or a thread-local deadline between tests."""
    deadline.uninstall_wait_callback()
    deadline.clear_deadline()
    yield
    deadline.uninstall_wait_callback()
    deadline.clear_deadline()


class _FakeConn:
    """A psycopg2-connection stand-in for driving the wait callback.

    ``poll()`` returns the next scripted state; ``fileno()`` returns a fd the
    test controls; ``poll()`` never touches the fd, so what the tests observe
    is the callback's own fd/timing behaviour.

    Models the status transitions the callback keys on, the way real psycopg2
    presents them: with ``connecting=True`` the status is ``STATUS_SETUP``
    before the first ``poll()``, then psycopg2's internal CONNECTING value (20,
    not exported) until the handshake completes with ``POLL_OK``, then
    ``STATUS_READY``. An optional ``on_poll`` hook runs before each scripted
    state is returned (used to emulate libpq swapping its socket).
    """
    _CONNECTING = 20
    # A callback that keeps polling a fd it should have refused (a broken fd
    # gate) must fail the test, not spin for the rest of the process.
    _MAX_POLLS = 10000

    def __init__(self, fd, states, connecting=False, dsn=None, on_poll=None):
        self._fd = fd
        self._states = list(states)
        self.connecting = connecting
        self.status = (psycopg2.extensions.STATUS_SETUP
                       if connecting else psycopg2.extensions.STATUS_READY)
        self._dsn = dsn or {}
        self._on_poll = on_poll
        self.polls = 0
        self.close_calls = 0

    def close(self):
        self.close_calls += 1

    def poll(self):
        self.polls += 1
        if self.polls > self._MAX_POLLS:
            raise RuntimeError('fake connection polled too often')
        if self.connecting:
            self.status = self._CONNECTING
        if self._on_poll is not None:
            self._on_poll(self, self.polls)
        state = (self._states.pop(0)
                 if self._states else psycopg2.extensions.POLL_READ)
        if state == psycopg2.extensions.POLL_OK and self.connecting:
            self.connecting = False
            self.status = psycopg2.extensions.STATUS_READY
        return state

    def fileno(self):
        return self._fd

    def get_dsn_parameters(self):
        return dict(self._dsn)


def _run_callback_in_thread(conn, deadline_seconds=None):
    """Run wait_callback(conn) in a daemon thread; return (thread, result)."""
    result = {}

    def worker():
        if deadline_seconds is not None:
            deadline.set_deadline(time.monotonic() + deadline_seconds)
        t0 = time.monotonic()
        try:
            deadline.wait_callback(conn)
            result.update(kind='returned', dt=time.monotonic() - t0)
        except BaseException as e:  # noqa: BLE001  pylint: disable=broad-except
            result.update(kind='raised',
                          dt=time.monotonic() - t0,
                          exc=type(e).__name__,
                          reason=getattr(e, 'reason', None),
                          msg=str(e).splitlines()[0][:80])
        finally:
            deadline.clear_deadline()

    t = threading.Thread(target=worker, daemon=True, name='cb-worker')
    t.start()
    return t, result


def _close_all(*objs):
    for o in objs:
        try:
            if isinstance(o, int):
                os.close(o)
            else:
                o.close()
        except OSError:
            pass


class TestNoInstallOnImport:
    """Green mode must not be a global side effect of importing auth."""

    def test_importing_db_lookup_leaves_no_wait_callback(self):
        # Fresh import name-check: the module is already imported by other
        # tests, so assert the invariant the install path must preserve.
        import sky.server.auth.db_lookup  # noqa: F401  pylint: disable=import-outside-toplevel,unused-import
        assert psycopg2.extensions.get_wait_callback() is None

    def test_install_and_uninstall(self):
        assert deadline.get_wait_callback() is None
        assert deadline.install_wait_callback() is True
        assert deadline.get_wait_callback() is deadline.wait_callback
        deadline.uninstall_wait_callback()
        assert deadline.get_wait_callback() is None

    def test_install_degrades_without_psycopg2(self, monkeypatch):
        # A sqlite-only deployment where psycopg2 does not import must keep
        # starting: no exception from the server lifespan, just a warning.
        monkeypatch.setattr(deadline, '_psycopg2_ext', None)
        with mock.patch.object(deadline.logger, 'warning') as warning:
            assert deadline.install_wait_callback() is False
        warning.assert_called_once()
        assert deadline.get_wait_callback() is None
        deadline.uninstall_wait_callback()  # no-op, no exception


class TestWaitCallbackDeadline:
    """Client side: the thread gives up at the deadline, never pins."""

    def test_never_readable_socket_returns_at_deadline(self):
        # A socketpair end that never receives data: poll(2) blocks until the
        # slice expires, the callback re-checks the deadline and raises.
        a, b = socket.socketpair()
        try:
            conn = _FakeConn(a.fileno(), [psycopg2.extensions.POLL_READ] * 100)
            t, result = _run_callback_in_thread(conn, deadline_seconds=0.5)
            t.join(3)
            assert not t.is_alive(), 'thread pinned past the deadline'
            assert result['kind'] == 'raised'
            assert result['exc'] == 'DBDeadlineExceeded'
            assert result['reason'] == 'client_deadline'
            assert 0.4 <= result['dt'] <= 1.6
            # After the handshake psycopg2 closes the connection itself on a
            # callback error (green_panic -> PQfinish); the callback must not.
            assert conn.close_calls == 0
        finally:
            _close_all(a, b)

    def test_no_deadline_waits_until_readable(self):
        a, b = socket.socketpair()
        try:
            conn = _FakeConn(
                a.fileno(),
                [psycopg2.extensions.POLL_READ, psycopg2.extensions.POLL_OK])
            t, result = _run_callback_in_thread(conn, deadline_seconds=None)
            time.sleep(0.3)
            assert t.is_alive(), 'callback returned before the socket was ready'
            b.send(b'x')  # make the fd readable -> poll wakes -> POLL_OK
            t.join(3)
            assert not t.is_alive()
            assert result['kind'] == 'returned'
        finally:
            _close_all(a, b)

    def test_conn_poll_is_not_called_after_an_expired_slice(self):
        # libpq requires PQconnectPoll/PQconsumeInput only when the socket is
        # ready; a slice that expires without readiness must only re-check the
        # deadline. With a 2.2s deadline and 1s slices that is >= 2 expiries.
        a, b = socket.socketpair()
        try:
            conn = _FakeConn(a.fileno(), [psycopg2.extensions.POLL_READ] * 100)
            t, result = _run_callback_in_thread(conn, deadline_seconds=2.2)
            t.join(5)
            assert not t.is_alive()
            assert result['kind'] == 'raised'
            assert conn.polls == 1, conn.polls
        finally:
            _close_all(a, b)


class TestStolenFd:
    """A stolen/closed fd is detected by identity, never handed to libpq.

    The design's earlier version let ``conn.poll()`` -> ``recv()`` run on the
    reused number; when it was a blocking socket the whole process froze. The
    fstat gate raises before touching the foreign fd, for every reuse kind.
    """

    def _steal(self, reuse_kind, deadline_seconds=3.0, sleep_first=0.5):
        a, b = socket.socketpair()
        fd = a.fileno()
        conn = _FakeConn(fd, [psycopg2.extensions.POLL_READ] * 1000)
        # Record identity as the callback does while the connection is made.
        deadline._sock_ident[conn] = deadline._ident(fd)
        t, result = _run_callback_in_thread(conn,
                                            deadline_seconds=deadline_seconds)
        time.sleep(sleep_first)  # let it park in poll()
        # detach() first: a socket object whose number was closed under it
        # would close that number AGAIN when collected -- and hit whatever
        # reused it (the exact double close this module is about).
        assert a.detach() == fd
        os.close(fd)
        keep = []
        if reuse_kind == 'pipe':
            r, w = os.pipe()
            keep = [r, w]
            reused = r
        elif reuse_kind == 'blocking-socket':
            c, d = socket.socketpair()  # blocking by default
            keep = [c, d]
            reused = c.fileno()
        elif reuse_kind == 'nonblocking-socket':
            c, d = socket.socketpair()
            c.setblocking(False)
            keep = [c, d]
            reused = c.fileno()
        else:  # closed, not reused
            reused = None
        return t, result, conn, fd, reused, (a, b), keep

    @pytest.mark.parametrize('reuse_kind',
                             ['pipe', 'blocking-socket', 'nonblocking-socket'])
    def test_stolen_fd_is_detected_and_does_not_freeze(self, reuse_kind):
        t, result, conn, fd, reused, ab, keep = self._steal(reuse_kind)
        try:
            # The callback must return within the deadline for EVERY reuse kind,
            # including a blocking socket (which would otherwise freeze the
            # thread inside recv() with the GIL held) -- and must not have
            # handed the foreign fd to libpq: no poll() after the theft.
            t.join(4)
            assert not t.is_alive(), (
                f'thread frozen on a {reuse_kind} that took the fd number')
            assert result['kind'] == 'raised'
            assert result['exc'] == 'DBDeadlineExceeded'
            if reused == fd:
                assert result['reason'] == 'client_deadline_fd_stolen'
            assert conn.polls == 1, 'conn.poll() ran on the stolen fd'
        finally:
            _close_all(*keep, *ab)

    def test_closed_fd_returns_fast(self):
        t, result, conn, _, _, ab, keep = self._steal('closed',
                                                      deadline_seconds=5.0,
                                                      sleep_first=0.4)
        try:
            t.join(3)
            assert not t.is_alive()
            assert result['kind'] == 'raised'
            assert result['reason'] == 'client_deadline_fd_closed'
            # Much faster than the 5s deadline: caught at the next slice.
            assert result['dt'] < 3.0
            assert conn.polls == 1
        finally:
            _close_all(*keep, *ab)

    def test_stolen_fd_is_caught_before_the_first_libpq_io(self):
        # Stolen BETWEEN two statements (the fd is not being waited on). The
        # first conn.poll() of the next wait would flush the statement -> a
        # send() into someone else's socket. The gate runs before it.
        a, b = socket.socketpair()
        fd = a.fileno()
        conn = _FakeConn(fd, [psycopg2.extensions.POLL_READ] * 10)
        deadline._sock_ident[conn] = deadline._ident(fd)
        assert a.detach() == fd
        os.close(fd)
        c, d = socket.socketpair()  # takes the number back
        try:
            assert c.fileno() == fd, 'test setup: fd number not reused'
            # A deadline only so that a broken gate fails (client_deadline
            # after 1s) instead of blocking here; the correct code raises at
            # once, before any poll.
            deadline.set_deadline(time.monotonic() + 1.0)
            with pytest.raises(deadline.DBDeadlineExceeded) as ei:
                deadline.wait_callback(conn)
            assert ei.value.reason == 'client_deadline_fd_stolen'
            assert conn.polls == 0
        finally:
            deadline.clear_deadline()
            _close_all(b, c, d)

    def test_stolen_fd_without_a_deadline_pins_until_the_wait_ends(self):
        """The incident's pin, with plain sockets: no deadline means libpq's
        poll(-1), and the kernel keeps the sleeping poll on the OLD socket.
        Data on it wakes the sleeper, which re-checks readiness by fd NUMBER
        (now the new owner), finds nothing and sleeps again; data on the new
        owner alone never wakes it. Only a bounded slice (a deadline) ends
        that early. When the wait does end, the gate runs before libpq sees
        the foreign fd."""
        a, b = socket.socketpair()
        fd = a.fileno()
        conn = _FakeConn(fd, [psycopg2.extensions.POLL_READ] * 10)
        deadline._sock_ident[conn] = deadline._ident(fd)
        t, result = _run_callback_in_thread(conn, deadline_seconds=None)
        time.sleep(0.3)
        assert a.detach() == fd
        os.close(fd)
        c, d = socket.socketpair()
        try:
            assert c.fileno() == fd, 'test setup: fd number not reused'
            b.send(b'x')  # the old socket is readable...
            time.sleep(0.5)
            assert t.is_alive(), 'expected the pin (re-check by fd number)'
            d.send(b'y')  # ...and now so is the new owner's; wake once more
            b.send(b'z')
            t.join(3)
            assert not t.is_alive()
            assert result['kind'] == 'raised'
            assert result['reason'] == 'client_deadline_fd_stolen'
            assert conn.polls == 1, 'conn.poll() ran on the stolen fd'
        finally:
            _close_all(b, c, d)


class TestConnectPhaseWithStandIn:
    """The handshake: connect_timeout enforced (and the earlier of it and the
    thread deadline wins), a failed connect closed at once, socket swaps
    tolerated."""

    def test_connect_timeout_bounds_a_never_answering_connect(self):
        # A socketpair end that never becomes readable, presented as a
        # connection in psycopg2's CONNECTING status (the value real psycopg2
        # shows inside the callback; STATUS_SETUP is gone after the first
        # poll) with connect_timeout=1. No thread deadline: the connect bound
        # is what must fire.
        a, b = socket.socketpair()
        try:
            conn = _FakeConn(a.fileno(), [psycopg2.extensions.POLL_READ] * 1000,
                             connecting=True,
                             dsn={'connect_timeout': '1'})
            t, result = _run_callback_in_thread(conn, deadline_seconds=None)
            t.join(4)
            assert not t.is_alive(), 'connect hung past connect_timeout'
            assert result['kind'] == 'raised'
            # An OperationalError (so is_disconnect / retry logic treat it as a
            # libpq connect timeout) carrying our reason.
            assert result['exc'] == 'DBDeadlineExceeded'
            assert result['reason'] == 'connect_timeout'
            assert 'timeout expired' in result['msg']
            assert 0.8 <= result['dt'] <= 2.5
            assert conn.polls == 1
        finally:
            _close_all(a, b)

    def test_no_connect_timeout_does_not_bound_connect(self):
        a, b = socket.socketpair()
        try:
            conn = _FakeConn(a.fileno(), [psycopg2.extensions.POLL_READ] * 1000,
                             connecting=True,
                             dsn={})  # no connect_timeout
            t, _ = _run_callback_in_thread(conn, deadline_seconds=None)
            time.sleep(1.2)
            assert t.is_alive(), 'connect bounded with no connect_timeout set'
            b.send(b'x')
            conn._states = [psycopg2.extensions.POLL_OK]
            t.join(3)
            assert not t.is_alive()
        finally:
            _close_all(a, b)

    def test_thread_deadline_applies_during_connect(self):
        a, b = socket.socketpair()
        try:
            conn = _FakeConn(a.fileno(), [psycopg2.extensions.POLL_READ] * 1000,
                             connecting=True)
            t, result = _run_callback_in_thread(conn, deadline_seconds=0.5)
            t.join(3)
            assert not t.is_alive()
            assert result['kind'] == 'raised'
            assert result['reason'] == 'client_deadline'
        finally:
            _close_all(a, b)

    def test_connect_timeout_wins_over_a_longer_thread_deadline(self):
        """Both bounds apply during the handshake and the earlier one fires:
        a connect_timeout shorter than the remaining thread budget ends the
        connect as libpq's own would (reason connect_timeout), not at the
        thread deadline."""
        a, b = socket.socketpair()
        try:
            conn = _FakeConn(a.fileno(), [psycopg2.extensions.POLL_READ] * 1000,
                             connecting=True,
                             dsn={'connect_timeout': '1'})
            t, result = _run_callback_in_thread(conn, deadline_seconds=5.0)
            t.join(4)
            assert not t.is_alive(), 'connect ran past connect_timeout'
            assert result['kind'] == 'raised'
            assert result['reason'] == 'connect_timeout'
            assert 0.8 <= result['dt'] <= 2.5, result
        finally:
            _close_all(a, b)

    def test_thread_deadline_wins_over_a_longer_connect_timeout(self):
        a, b = socket.socketpair()
        try:
            conn = _FakeConn(a.fileno(), [psycopg2.extensions.POLL_READ] * 1000,
                             connecting=True,
                             dsn={'connect_timeout': '5'})
            t, result = _run_callback_in_thread(conn, deadline_seconds=0.5)
            t.join(4)
            assert not t.is_alive(), 'connect ran past the thread deadline'
            assert result['kind'] == 'raised'
            assert result['reason'] == 'client_deadline'
            assert 0.3 <= result['dt'] <= 2.0, result
        finally:
            _close_all(a, b)

    @pytest.mark.parametrize('connect_timeout, deadline_seconds, reason', [
        ('1', None, 'connect_timeout'),
        (None, 0.5, 'client_deadline'),
    ])
    def test_a_connect_deadline_closes_the_connection_at_once(
            self, connect_timeout, deadline_seconds, reason):
        """psycopg2 PQfinish'es a connect that failed inside the callback
        only when the connection object is deallocated, which the exception's
        traceback (holding the callback's frame, hence the connection) delays
        to the next cyclic GC pass. The callback closes it itself, for either
        bound, so the half-open socket is not held until then."""
        dsn = {'connect_timeout': connect_timeout} if connect_timeout else {}
        a, b = socket.socketpair()
        try:
            conn = _FakeConn(a.fileno(), [psycopg2.extensions.POLL_READ] * 1000,
                             connecting=True,
                             dsn=dsn)
            t, result = _run_callback_in_thread(
                conn, deadline_seconds=deadline_seconds)
            t.join(4)
            assert not t.is_alive()
            assert result['kind'] == 'raised'
            assert result['reason'] == reason
            assert conn.close_calls == 1
        finally:
            _close_all(a, b)

    def test_handshake_socket_swap_is_not_a_stolen_fd(self):
        # libpq closes and reopens its socket inside PQconnectPoll (address
        # fallback, multi-host DSNs); the new socket reuses the fd NUMBER, the
        # inode changes. The gate must not fire during the handshake, and the
        # identity kept afterwards must be the socket libpq ended up with.
        a, b = socket.socketpair()
        fd = a.fileno()
        swapped = {}

        def on_poll(conn, n):
            del conn
            if n == 2:
                # The "fallback": drop the first socket, open the second on the
                # same number, and make it readable for the READ step.
                a.close()
                c, d = socket.socketpair()
                assert c.fileno() == fd, 'test setup: fd number not reused'
                d.send(b'x')
                swapped.update(c=c, d=d)

        conn = _FakeConn(fd, [
            psycopg2.extensions.POLL_WRITE, psycopg2.extensions.POLL_WRITE,
            psycopg2.extensions.POLL_READ, psycopg2.extensions.POLL_OK
        ],
                         connecting=True,
                         on_poll=on_poll)
        try:
            t, result = _run_callback_in_thread(conn, deadline_seconds=5.0)
            t.join(4)
            assert not t.is_alive()
            assert result['kind'] == 'returned', result
            assert conn.status == psycopg2.extensions.STATUS_READY
            # The identity kept is the second socket's.
            assert deadline._sock_ident[conn] == deadline._ident(
                swapped['c'].fileno())
            # ...so a theft AFTER the handshake is still caught.
            swapped['c'].close()
            e, f = socket.socketpair()
            assert e.fileno() == fd
            conn._states = [psycopg2.extensions.POLL_READ] * 10
            deadline.set_deadline(time.monotonic() + 1.0)  # see above
            with pytest.raises(deadline.DBDeadlineExceeded) as ei:
                deadline.wait_callback(conn)
            deadline.clear_deadline()
            assert ei.value.reason == 'client_deadline_fd_stolen'
            _close_all(e, f)
        finally:
            _close_all(b, swapped.get('d'))


def _refused_socket() -> socket.socket:
    """A bound, NOT listening TCP socket: a connect to its port is refused
    (RST -> ECONNREFUSED) for as long as it stays open. Held open by the test
    rather than bound-read-closed, so nothing else can take the port meanwhile.
    """
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    return s


def _recv_until_eof(sock: socket.socket, timeout: float) -> bool:
    """Drain ``sock``; True if the peer closed it (EOF) within ``timeout``."""
    sock.settimeout(timeout)
    try:
        while True:
            if not sock.recv(4096):
                return True
    except socket.timeout:
        return False


def _listener():
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('127.0.0.1', 0))
    s.listen(4)
    return s


def _dsn(ports, **extra):
    hosts = ','.join('127.0.0.1' for _ in ports)
    parts = [
        f'host={hosts}', f'port={",".join(str(p) for p in ports)}', 'user=sky',
        'dbname=sky', 'sslmode=disable', 'gssencmode=disable'
    ]
    parts += [f'{k}={v}' for k, v in extra.items()]
    return ' '.join(parts)


# A connect that the code under test fails to bound would block the test
# process forever (a CI job timeout, not a red test). Every real-libpq connect
# below runs in a worker thread and is given this long to finish.
_CONNECT_TEST_TIMEOUT = 10.0


def _connect_in_thread(dsn, deadline_seconds=None):
    """psycopg2.connect(dsn) on a worker thread; (elapsed, exception or None).

    Fails the test if the connect does not finish within
    ``_CONNECT_TEST_TIMEOUT`` -- that IS the defect these tests exist for.
    """
    result = {}

    def worker():
        if deadline_seconds is not None:
            deadline.set_deadline(time.monotonic() + deadline_seconds)
        t0 = time.monotonic()
        try:
            psycopg2.connect(dsn).close()
            result['exc'] = None
        except Exception as e:  # pylint: disable=broad-except
            result['exc'] = e
        finally:
            result['dt'] = time.monotonic() - t0
            deadline.clear_deadline()

    t = threading.Thread(target=worker, daemon=True, name='connect-worker')
    t.start()
    t.join(_CONNECT_TEST_TIMEOUT)
    assert not t.is_alive(), (
        f'psycopg2.connect did not return within {_CONNECT_TEST_TIMEOUT}s: '
        f'the connect is not bounded')
    return result['dt'], result['exc']


class TestConnectPhaseWithRealLibpq:
    """Real psycopg2/libpq, no database server: local listeners stand in.

    A multi-host DSN whose first host refuses makes libpq swap sockets inside
    the handshake (the fd number is reused). The second host either never
    answers (the connect bound must fire, and it must NOT be reported as a
    stolen fd) or hangs up (libpq's own error, i.e. the handshake proceeded on
    the new socket).
    """

    def test_fallback_then_connect_timeout(self):
        silent = _listener()  # accepts in the kernel, never answers
        refused = _refused_socket()
        try:
            dsn = _dsn([refused.getsockname()[1],
                        silent.getsockname()[1]],
                       connect_timeout=1)
            deadline.install_wait_callback()
            dt, exc = _connect_in_thread(dsn)
            assert isinstance(exc, psycopg2.OperationalError), exc
            assert deadline.deadline_reason(exc) == 'connect_timeout', exc
            assert 0.8 <= dt <= 2.5, dt
            # Parity with sync psycopg2 (libpq's own connect_timeout).
            deadline.uninstall_wait_callback()
            dt, exc = _connect_in_thread(dsn)
            assert isinstance(exc, psycopg2.OperationalError), exc
            assert 0.8 <= dt <= 2.5, dt
        finally:
            silent.close()
            refused.close()

    def test_fallback_reaches_the_second_host(self):
        hangup = _listener()
        refused = _refused_socket()

        def _accept_and_close():
            try:
                c, _ = hangup.accept()
                c.close()
            except OSError:
                pass

        th = threading.Thread(target=_accept_and_close, daemon=True)
        th.start()
        try:
            dsn = _dsn([refused.getsockname()[1],
                        hangup.getsockname()[1]],
                       connect_timeout=8)
            deadline.install_wait_callback()
            dt, exc = _connect_in_thread(dsn)
            # libpq's error from talking to the second host -- not ours...
            assert isinstance(exc, psycopg2.OperationalError), exc
            assert not isinstance(exc, deadline.DBDeadlineExceeded), exc
            assert deadline.deadline_reason(exc) is None
            # ...and promptly: a fallback that did not happen would only end
            # at connect_timeout (8 s). Loose bound for loaded runners.
            assert dt < 5.0, dt
        finally:
            hangup.close()
            refused.close()
            th.join(2)

    def test_thread_deadline_bounds_a_silent_connect(self):
        silent = _listener()
        try:
            dsn = _dsn([silent.getsockname()[1]])  # no connect_timeout
            deadline.install_wait_callback()
            dt, exc = _connect_in_thread(dsn, deadline_seconds=0.7)
            # Propagates intact from psycopg2.connect (class, reason).
            assert isinstance(exc, deadline.DBDeadlineExceeded), exc
            assert exc.reason == 'client_deadline'
            assert isinstance(exc, psycopg2.OperationalError)
            assert 0.5 <= dt <= 2.0, dt
        finally:
            silent.close()

    def test_connect_deadline_closes_the_socket_at_once(self):
        """A connect that gives up in the callback is closed right there, as
        sync psycopg2 closes a timed-out connect. psycopg2 otherwise
        PQfinish'es a failed connect only when the connection object is
        deallocated, and the exception's traceback keeps that object alive --
        here for as long as the test holds `exc`, in a server until the next
        cyclic GC pass. Observed from the peer: the accepted socket sees EOF
        (after libpq's startup packet) while `exc` is still referenced."""
        silent = _listener()
        accepted: list = []

        def _accept():
            try:
                c, _ = silent.accept()
                accepted.append(c)
            except OSError:
                pass

        th = threading.Thread(target=_accept, daemon=True)
        th.start()
        try:
            deadline.install_wait_callback()
            dt, exc = _connect_in_thread(_dsn([silent.getsockname()[1]]),
                                         deadline_seconds=0.3)
            assert isinstance(exc, deadline.DBDeadlineExceeded), exc
            assert exc.reason == 'client_deadline'
            assert 0.2 <= dt <= 2.0, dt
            th.join(2)
            assert accepted, 'libpq never reached the listener'
            assert _recv_until_eof(accepted[0], timeout=2.0), (
                'client socket still open after the connect deadline')
            del exc
        finally:
            silent.close()
            th.join(2)
            for c in accepted:
                c.close()


_PREFIX_RE = re.compile(r'^(SET LOCAL [^;]+; )+')


class _PsycopgLikeCursor(sqlite3.Cursor):
    """Flips the connection to BEGIN on the first statement, like psycopg2."""

    def execute(self, *args, **kwargs):
        result = super().execute(*args, **kwargs)
        self.connection.status = psycopg2.extensions.STATUS_BEGIN
        return result

    def executemany(self, *args, **kwargs):
        result = super().executemany(*args, **kwargs)
        self.connection.status = psycopg2.extensions.STATUS_BEGIN
        return result


class _PsycopgLikeConnection(sqlite3.Connection):
    """A sqlite3 connection presenting psycopg2's transaction-state surface.

    The listener reads two driver attributes: ``status`` (READY before the
    first statement of a transaction, BEGIN until commit/rollback) and
    ``autocommit`` (a bool on psycopg2; Python 3.12+ sqlite3 has its own
    int-valued ``autocommit`` -- LEGACY_TRANSACTION_CONTROL is -1 -- shadowed
    here). Everything else -- events, ``retval``, autobegin, pooling -- is
    real SQLAlchemy machinery on a real DBAPI connection.
    """
    autocommit = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.status = psycopg2.extensions.STATUS_READY

    def cursor(self, factory=None):
        return super().cursor(factory or _PsycopgLikeCursor)

    def commit(self):
        super().commit()
        self.status = psycopg2.extensions.STATUS_READY

    def rollback(self):
        super().rollback()
        self.status = psycopg2.extensions.STATUS_READY


class TestSetLocalListener:
    """The engine listener: attach conditions, emitted text, reset."""

    def _spy_engine(self,
                    url='sqlite://',
                    pretend_postgres=False,
                    before_install=None):
        """A sqlite engine, optionally presenting as Postgres + psycopg2.

        With ``pretend_postgres`` the listener attaches (it keys on the
        dialect name and driver) and the DBAPI connections are
        ``_PsycopgLikeConnection``; a second listener registered after it
        records what the driver would receive and strips the prefix again so
        sqlite can run the statement. ``before_install(engine)`` registers
        other listeners ahead of ours (listener-order tests).
        """
        if pretend_postgres:
            engine = sqlalchemy.create_engine(
                url,
                creator=lambda: sqlite3.connect(':memory:',
                                                factory=_PsycopgLikeConnection,
                                                check_same_thread=False))
            engine.dialect.name = 'postgresql'
            engine.dialect.driver = 'psycopg2'
        else:
            engine = sqlalchemy.create_engine(url)
        if before_install is not None:
            before_install(engine)
        deadline.install(engine)
        emitted = []

        @sqlalchemy.event.listens_for(engine,
                                      'before_cursor_execute',
                                      retval=True)
        def _capture(conn, cursor, statement, parameters, context, executemany):  # pylint: disable=unused-variable
            del conn, cursor, context, executemany
            emitted.append(statement)
            return _PREFIX_RE.sub('', statement), parameters

        return engine, emitted

    @staticmethod
    def _prefixes(emitted):
        return [s for s in emitted if s.startswith('SET LOCAL ')]

    def test_sqlite_engine_gets_no_listener_and_no_set_local(self):
        engine, emitted = self._spy_engine('sqlite://')
        deadline.set_deadline(time.monotonic() + 5)
        with engine.begin() as conn:
            conn.execute(sqlalchemy.text('SELECT 1'))
        assert not self._prefixes(emitted), emitted
        assert not deadline.is_bounding(engine)

    def test_other_postgres_driver_gets_no_listener(self):
        # The listener reads psycopg2's connection status; on another driver
        # it would silently never fire, so it is not attached at all.
        engine = sqlalchemy.create_engine('sqlite://')
        engine.dialect.name = 'postgresql'
        engine.dialect.driver = 'psycopg'
        deadline.install(engine)
        deadline.set_deadline(time.monotonic() + 5)
        assert not deadline.is_bounding(engine)

    def test_install_is_idempotent(self):
        engine, emitted = self._spy_engine(pretend_postgres=True)
        deadline.install(engine)  # second call: no second listener
        deadline.set_deadline(time.monotonic() + 4.5)
        with engine.begin() as conn:
            conn.execute(sqlalchemy.text('SELECT 1'))
        assert len(emitted) == 1
        assert len(_PREFIX_RE.match(emitted[0]).group(0).split('; ')) == 4

    def test_no_deadline_emits_no_set_local(self):
        engine, emitted = self._spy_engine(pretend_postgres=True)
        with engine.begin() as conn:
            conn.execute(sqlalchemy.text('SELECT 1'))
        assert not self._prefixes(emitted), emitted
        assert not deadline.is_bounding(engine)

    def test_first_statement_of_a_bounded_transaction_gets_the_prefix(self):
        engine, emitted = self._spy_engine(pretend_postgres=True)
        deadline.set_deadline(time.monotonic() + 4.5)
        with engine.begin() as conn:
            conn.execute(sqlalchemy.text('SELECT 1'))
            conn.execute(sqlalchemy.text('SELECT 2'))
        assert len(emitted) == 2
        first, second = emitted[0], emitted[1]
        assert first.startswith('SET LOCAL statement_timeout = ')
        assert 'SET LOCAL lock_timeout = ' in first
        assert 'SET LOCAL idle_in_transaction_session_timeout = ' in first
        assert first.endswith('; SELECT 1')
        # Only the first statement of the transaction.
        assert second == 'SELECT 2'

    def test_a_new_transaction_is_prefixed_again(self):
        # Same DBAPI connection (one Connection held open), three transactions
        # in a row: bounded, unbounded, bounded. COMMIT puts the driver back to
        # READY, so the third gets the prefix although the connection already
        # carried it once -- no per-connection mark to go stale.
        engine, emitted = self._spy_engine(pretend_postgres=True)
        with engine.connect() as conn:
            deadline.set_deadline(time.monotonic() + 4.5)
            with conn.begin():
                conn.execute(sqlalchemy.text('SELECT 1'))
            deadline.clear_deadline()
            with conn.begin():
                conn.execute(sqlalchemy.text('SELECT 2'))
            deadline.set_deadline(time.monotonic() + 4.5)
            with conn.begin():
                conn.execute(sqlalchemy.text('SELECT 3'))
                conn.execute(sqlalchemy.text('SELECT 4'))
        bare = [_PREFIX_RE.sub('', s) for s in emitted]
        assert bare == ['SELECT 1', 'SELECT 2', 'SELECT 3', 'SELECT 4']
        prefixed = [s.startswith('SET LOCAL') for s in emitted]
        assert prefixed == [True, False, True, False]

    def test_session_autobegin_is_prefixed(self):
        engine, emitted = self._spy_engine(pretend_postgres=True)
        deadline.set_deadline(time.monotonic() + 4.5)
        with orm.Session(engine) as session:
            session.execute(sqlalchemy.text('SELECT 1'))
            session.execute(sqlalchemy.text('SELECT 2'))
            session.commit()
        prefixed = [s.startswith('SET LOCAL') for s in emitted]
        assert prefixed == [True, False]

    def test_executemany_is_not_prefixed(self):
        engine, emitted = self._spy_engine(pretend_postgres=True)
        with engine.begin() as conn:
            conn.execute(sqlalchemy.text('CREATE TABLE t (x INTEGER)'))
        emitted.clear()
        deadline.set_deadline(time.monotonic() + 4.5)
        with engine.begin() as conn:
            # Opens the transaction; per-row text, so no prefix. The
            # transaction is then open (BEGIN): the next statement is not its
            # first and gets no prefix either -- a transaction OPENED by an
            # executemany has no server-side bound (documented; the auth path
            # never does this).
            conn.execute(sqlalchemy.text('INSERT INTO t (x) VALUES (:x)'), [{
                'x': 1
            }, {
                'x': 2
            }])
            conn.execute(sqlalchemy.text('SELECT count(*) FROM t'))
        with engine.begin() as conn:
            # A new transaction is bounded again.
            conn.execute(sqlalchemy.text('SELECT count(*) FROM t'))
        prefixed = [s.startswith('SET LOCAL') for s in emitted]
        assert prefixed == [False, False, True], emitted

    @pytest.mark.parametrize('other_listener_first', [True, False])
    def test_exactly_one_prefix_with_a_begin_listener_in_either_order(
            self, other_listener_first):
        # Another engine listener runs a statement from the `begin` event
        # (the shape of an engine-wide idle-in-transaction bound). Whatever
        # the registration order, that statement is the transaction's first
        # and carries the one prefix; the caller's own statements do not.
        def other(engine):

            @sqlalchemy.event.listens_for(engine, 'begin')
            def _begin(conn):  # pylint: disable=unused-variable
                conn.exec_driver_sql('SELECT 42').close()

        if other_listener_first:
            engine, emitted = self._spy_engine(pretend_postgres=True,
                                               before_install=other)
        else:
            engine, emitted = self._spy_engine(pretend_postgres=True)
            other(engine)
        deadline.set_deadline(time.monotonic() + 4.5)
        with engine.begin() as conn:
            conn.execute(sqlalchemy.text('SELECT 1'))
            conn.execute(sqlalchemy.text('SELECT 2'))
        with engine.begin() as conn:  # and again for the next transaction
            conn.execute(sqlalchemy.text('SELECT 3'))
        bare = [_PREFIX_RE.sub('', s) for s in emitted]
        assert bare == [
            'SELECT 42', 'SELECT 1', 'SELECT 2', 'SELECT 42', 'SELECT 3'
        ]
        prefixed = [s.startswith('SET LOCAL') for s in emitted]
        assert prefixed == [True, False, False, True, False], emitted

    def test_is_bounding_needs_the_listener_and_a_deadline(self):
        engine, _ = self._spy_engine(pretend_postgres=True)
        assert not deadline.is_bounding(engine)
        deadline.set_deadline(time.monotonic() + 4.5)
        assert deadline.is_bounding(engine)
        deadline.clear_deadline()
        assert not deadline.is_bounding(engine)
        # An engine without the listener is never "bounding", deadline or not.
        deadline.set_deadline(time.monotonic() + 4.5)
        assert not deadline.is_bounding(sqlalchemy.create_engine('sqlite://'))

    def _stub_conn(self, autocommit=False, status='ready'):
        conn = mock.Mock()
        conn.info = {}
        conn.connection.dbapi_connection.autocommit = autocommit
        conn.connection.dbapi_connection.status = (
            psycopg2.extensions.STATUS_READY
            if status == 'ready' else psycopg2.extensions.STATUS_BEGIN)
        return conn

    def test_named_cursor_is_not_prefixed(self):
        # A server-side cursor wraps the text in DECLARE ... FOR <statement>;
        # a leading SET LOCAL there is a syntax error.
        deadline.set_deadline(time.monotonic() + 4.5)
        conn = self._stub_conn()
        cursor = mock.Mock()
        cursor.name = 'stream'
        assert deadline._bounded_statement(conn, cursor, 'SELECT 1',
                                           False) == 'SELECT 1'
        cursor.name = None
        assert deadline._bounded_statement(conn, cursor, 'SELECT 1',
                                           False).endswith('; SELECT 1')

    def test_autocommit_connection_is_not_prefixed(self):
        deadline.set_deadline(time.monotonic() + 4.5)
        conn = self._stub_conn(autocommit=True)
        cursor = mock.Mock()
        cursor.name = None
        assert deadline._bounded_statement(conn, cursor, 'SELECT 1',
                                           False) == 'SELECT 1'

    def test_an_open_transaction_is_not_prefixed_again(self):
        deadline.set_deadline(time.monotonic() + 4.5)
        conn = self._stub_conn(status='begin')
        cursor = mock.Mock()
        cursor.name = None
        assert deadline._bounded_statement(conn, cursor, 'SELECT 1',
                                           False) == 'SELECT 1'

    def test_a_connection_without_a_driver_status_is_left_alone(self):
        deadline.set_deadline(time.monotonic() + 4.5)
        conn = self._stub_conn()
        del conn.connection.dbapi_connection.status
        cursor = mock.Mock()
        cursor.name = None
        assert deadline._bounded_statement(conn, cursor, 'SELECT 1',
                                           False) == 'SELECT 1'

    def test_prefix_is_sized_from_the_remaining_budget(self):
        deadline.set_deadline(time.monotonic() + 2.0)
        conn = self._stub_conn()
        cursor = mock.Mock()
        cursor.name = None
        text = deadline._bounded_statement(conn, cursor, 'SELECT 1', False)
        statement_ms = int(
            re.search(r'statement_timeout = (\d+)', text).group(1))
        # 2000 remaining - 500 server margin, minus the microseconds elapsed.
        assert 1400 <= statement_ms <= 1500, text

    def test_prefix_values_are_ordered(self):
        text = deadline._set_local_prefix(4500)

        def _val(name):
            return int(re.search(rf'{name} = (\d+)', text).group(1))

        assert _val('lock_timeout') < _val('statement_timeout')
        assert (_val('idle_in_transaction_session_timeout') >=
                _val('statement_timeout'))
        # statement fires the server margin before the client deadline.
        assert _val('statement_timeout') == 4500 - deadline._SERVER_MARGIN_MS
        assert '%' not in text

    def test_prefix_values_floor_at_minimum(self):
        text = deadline._set_local_prefix(10)  # tiny remaining budget

        def _val(name):
            return int(re.search(rf'{name} = (\d+)', text).group(1))

        assert _val('statement_timeout') == deadline._MIN_TIMEOUT_MS
        assert _val('lock_timeout') == deadline._MIN_TIMEOUT_MS


class _PgError(Exception):
    """A server error stand-in carrying a SQLSTATE (psycopg2's C error type
    keeps `pgcode` read-only; deadline_reason only reads `orig.pgcode`)."""

    def __init__(self, code):
        super().__init__('cancelled')
        self.pgcode = code


class TestClassification:
    """deadline_reason / is_deadline_error / is_transient_driver_error."""

    def test_wrapped_client_deadline(self):
        orig = deadline.DBDeadlineExceeded('x', reason='client_deadline')
        wrapped = sqlalchemy.exc.OperationalError('stmt', {}, orig)
        assert deadline.deadline_reason(wrapped) == 'client_deadline'
        assert deadline.is_deadline_error(wrapped)

    @pytest.mark.parametrize('reason', [
        'client_deadline_fd_stolen', 'client_deadline_fd_closed',
        'connect_timeout'
    ])
    def test_wrapped_client_reasons(self, reason):
        orig = deadline.DBDeadlineExceeded('x', reason=reason)
        wrapped = sqlalchemy.exc.OperationalError('stmt', {}, orig)
        assert deadline.deadline_reason(wrapped) == reason

    @pytest.mark.parametrize('pgcode,reason',
                             [('57014', 'statement_timeout'),
                              ('55P03', 'lock_timeout'),
                              ('25P03', 'idle_in_transaction')])
    def test_server_pgcodes(self, pgcode, reason):
        wrapped = sqlalchemy.exc.OperationalError('stmt', {}, _PgError(pgcode))
        assert deadline.deadline_reason(wrapped) == reason
        assert deadline.is_deadline_error(wrapped)

    def test_idle_session_timeout_is_not_ours(self):
        # 57P05 is idle_session_timeout, which the bounds never set.
        wrapped = sqlalchemy.exc.OperationalError('stmt', {}, _PgError('57P05'))
        assert deadline.deadline_reason(wrapped) is None

    def test_non_deadline_error(self):
        orig = psycopg2.OperationalError('connection dropped')
        wrapped = sqlalchemy.exc.OperationalError('stmt', {}, orig)
        assert deadline.deadline_reason(wrapped) is None
        assert not deadline.is_deadline_error(wrapped)

    @pytest.mark.parametrize('orig,transient', [
        (psycopg2.OperationalError('server closed the connection'), True),
        (psycopg2.InterfaceError('connection already closed'), True),
        (deadline.DBDeadlineExceeded('x'), True),
        (sqlite3.OperationalError('no such table: users'), False),
        (_PgError(None), False),
        (Exception('x'), False),
    ])
    def test_transient_driver_error_is_psycopg2_only(self, orig, transient):
        wrapped = sqlalchemy.exc.OperationalError('stmt', {}, orig)
        assert deadline.is_transient_driver_error(wrapped) is transient
        assert deadline.is_transient_driver_error(orig) is transient
