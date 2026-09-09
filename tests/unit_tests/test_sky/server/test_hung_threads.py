"""Unit tests for sky/server/hung_threads.py (Linux /proc based)."""
import os
import select
import socket
import sys
import threading
import time

import pytest

from sky.server import hung_threads

linux_only = pytest.mark.skipif(sys.platform != 'linux',
                                reason='/proc is Linux only')


def _blocked_in_poll(fd, ready: threading.Event):
    poller = select.poll()
    poller.register(fd, select.POLLIN)
    ready.set()
    poller.poll(-1)


def _blocked_in_recv(sock, ready: threading.Event):
    ready.set()
    sock.recv(1)


def _start_blocked(target, arg):
    ready = threading.Event()
    thread = threading.Thread(target=target, args=(arg, ready), daemon=True)
    thread.start()
    ready.wait(5)
    time.sleep(0.1)  # let it enter the syscall
    return thread


@pytest.fixture(name='blocked')
def _blocked_fixture(request):
    target = request.param
    a, b = socket.socketpair()
    thread = _start_blocked(target,
                            a.fileno() if target is _blocked_in_poll else a)
    yield thread, a
    b.sendall(b'x')
    thread.join(5)
    a.close()
    b.close()


def _open_fds():
    return len(os.listdir('/proc/self/fd'))


@linux_only
@pytest.mark.parametrize('blocked', [_blocked_in_poll], indirect=True)
def test_kernel_view_decodes_poll(blocked):
    thread, sock = blocked
    view = hung_threads.kernel_view(thread.native_id)
    assert view['state'] == 'S'
    assert view['syscall'] in ('poll', 'ppoll')
    assert sock.fileno() in view['fds']
    assert view['pollfds'] and f'fd={sock.fileno()}' in view['pollfds'][0]
    assert view['timeout'] == 'infinite'


@linux_only
@pytest.mark.parametrize('blocked', [_blocked_in_recv], indirect=True)
def test_kernel_view_decodes_recv(blocked):
    thread, sock = blocked
    view = hung_threads.kernel_view(thread.native_id)
    assert view['syscall'] in ('recvfrom', 'read', 'recvmsg')
    assert view['fds'] == [sock.fileno()]


@linux_only
def test_describe_fd_socket_and_closed():
    a, b = socket.socketpair()
    try:
        desc = hung_threads.describe_fd(a.fileno())
        assert desc.startswith(f'fd {a.fileno()}: socket:[')
        assert 'unix' in desc and 'recv-q=0' in desc
    finally:
        fd = a.fileno()
        a.close()
        b.close()
    assert 'not open' in hung_threads.describe_fd(fd)


@linux_only
def test_describe_fd_tcp_peer_state_and_queues():
    srv = socket.socket()
    srv.bind(('127.0.0.1', 0))
    srv.listen(1)
    cli = socket.create_connection(srv.getsockname())
    acc, _ = srv.accept()
    before = _open_fds()
    try:
        desc = hung_threads.describe_fd(cli.fileno())
        assert 'tcp' in desc and 'ESTABLISHED' in desc
        assert f'-> 127.0.0.1:{srv.getsockname()[1]}' in desc
        assert 'recv-q=0' in desc
        # Unread bytes from the peer show up as the receive queue: the
        # discriminator between "reply never arrived" and "reply arrived and
        # was never read".
        acc.sendall(b'x' * 68)
        time.sleep(0.05)
        assert 'recv-q=68' in hung_threads.describe_fd(cli.fileno())
        # The inspection works on a dup and leaks nothing.
        assert _open_fds() == before
    finally:
        cli.close()
        acc.close()
        srv.close()


@linux_only
def test_describe_fd_non_socket():
    r, w = os.pipe()
    before = _open_fds()
    try:
        desc = hung_threads.describe_fd(r)
        assert desc.startswith(f'fd {r}: pipe:[')
        assert 'recv-q' not in desc
        assert hung_threads.socket_info(r) is None
        assert _open_fds() == before
    finally:
        os.close(r)
        os.close(w)


@pytest.mark.parametrize('blocked', [_blocked_in_poll], indirect=True)
def test_python_stack_names_blocking_frame(blocked):
    thread, _ = blocked
    lines = hung_threads.python_stack(thread)
    assert any('_blocked_in_poll' in l for l in lines)


@pytest.mark.parametrize('blocked', [_blocked_in_poll], indirect=True)
def test_describe_is_bounded_and_never_raises(blocked):
    thread, sock = blocked
    text = hung_threads.describe(thread,
                                 12.5,
                                 task='mod.fn',
                                 deadline_seconds=5)
    assert text.startswith(f'thread {thread.name!r}')
    assert 'running 12.5s (limit 5s) task=mod.fn' in text
    assert 'python stack' in text
    if sys.platform == 'linux':
        assert 'syscall=' in text
        assert f'fd {sock.fileno()}: socket:[' in text
    assert len(text) < 8192


def test_kernel_view_bad_tid_is_empty():
    assert hung_threads.kernel_view(2**30) == {'wchan': None}


def test_peer_hex_forms():
    # pylint: disable=protected-access
    forms = hung_threads._peer_hex_forms([('127.0.0.1', 6432)])
    assert '0100007F:1920' in forms
    assert '0000000000000000FFFF00000100007F:1920' in forms  # tcp6 v4-mapped
    forms = hung_threads._peer_hex_forms([('::1', 5432)])
    assert '00000000000000000000000001000000:1538' in forms
    assert hung_threads._peer_hex_forms([('no.such.host.invalid', 1)]) == set()


def test_hex_addr():
    # pylint: disable=protected-access
    assert hung_threads._hex_addr('0100007F:1920') == '127.0.0.1:6432'
    assert hung_threads._hex_addr(
        '00000000000000000000000001000000:1538') == '[::1]:5432'
    assert hung_threads._hex_addr('garbage') == 'garbage'


@linux_only
def test_scan_finds_socket_closed_under_a_sleeping_poll():
    """Reproduce, without a database, the failure the scan exists for: a
    thread sleeps in poll() on a TCP socket; another thread closes that
    descriptor number and the number is reused; the peer then sends a reply.
    The connection stays established with the reply unread and no descriptor
    pointing at it. The per-thread view now shows the new owner of the
    number; only the scan shows the orphan."""
    srv = socket.socket()
    srv.bind(('127.0.0.1', 0))
    srv.listen(5)
    port = srv.getsockname()[1]
    # A healthy connection to the same peer, so the peer counts as one this
    # host talks to (the scan is scoped to such peers).
    healthy = socket.create_connection(srv.getsockname())
    healthy_acc, _ = srv.accept()
    victim = socket.create_connection(srv.getsockname())
    victim_acc, _ = srv.accept()
    victim_port = victim.getsockname()[1]
    fd = victim.detach()  # the raw number, owned by this test now
    thread = _start_blocked(_blocked_in_poll, fd)
    pipe = None
    try:
        lines = hung_threads.scan_ownerless_sockets()
        assert lines[0].startswith('ownerless sockets: ')
        assert not any(f'127.0.0.1:{victim_port} ->' in l for l in lines)

        os.close(fd)  # the stray close
        pipe = os.pipe()  # the next allocation takes the number
        assert pipe[0] == fd
        victim_acc.sendall(b'x' * 68)  # the reply, landing unread
        time.sleep(0.1)
        assert thread.is_alive()

        # The thread's descriptor is now the pipe: nothing there says a
        # socket is missing.
        assert hung_threads.describe_fd(fd).startswith(f'fd {fd}: pipe:[')
        lines = hung_threads.scan_ownerless_sockets()
        hits = [l for l in lines if f'127.0.0.1:{victim_port} ->' in l]
        assert len(hits) == 1, lines
        assert f'-> 127.0.0.1:{port} ESTABLISHED' in hits[0]
        assert 'recv-q=68' in hits[0]
        assert 'owner=none' in hits[0]
        assert ' ms)' in lines[0]

        # Without any owned connection to the peer the orphan is invisible
        # (nothing says this host talks to that peer) -- unless the caller
        # names the peer, which is what the executor does for the database.
        healthy.close()
        healthy_acc.close()
        time.sleep(0.05)
        lines = hung_threads.scan_ownerless_sockets()
        assert not any(f'127.0.0.1:{victim_port} ->' in l for l in lines)
        lines = hung_threads.scan_ownerless_sockets(extra_peers=[('localhost',
                                                                  port)])
        hits = [l for l in lines if f'127.0.0.1:{victim_port} ->' in l]
        assert len(hits) == 1 and 'recv-q=68' in hits[0], lines
    finally:
        if pipe is not None:
            os.write(pipe[1], b'x')  # wakes the poller (it polls the pipe)
            thread.join(5)
            os.close(pipe[0])
            os.close(pipe[1])
        for s in (victim_acc, srv):
            s.close()
    # With the poll gone the kernel dropped the orphan's last reference.
    time.sleep(0.1)
    lines = hung_threads.scan_ownerless_sockets()
    assert not any(f'127.0.0.1:{victim_port} ->' in l for l in lines)
