"""Tests for orphaned queue manager recovery on port conflict.

Background: a SkyPilot API server starts its request queue manager as a
multiprocessing subprocess on a fixed port. If the API server dies abruptly
(SIGKILL / OOM), the subprocess is reparented to init (PID 1) and keeps
running, blocking later attempts to start the API server with "port already
in use". This is the cause of repeated SkyServe AWS replica FAILED_PROVISION
on long-lived controller VMs (see PR #9719).

These tests exercise the recovery path in
sky/server/requests/queues/base.py:_reap_orphan_queue_manager and the
PR_SET_PDEATHSIG installation in mp_queue.start_queue_manager.
"""
import multiprocessing
import os
import signal
import socket
import sys
import time
from unittest import mock

import pytest

from sky.server.requests.queues import base as queue_base
from sky.server.requests.queues import mp_queue
from sky.utils import common_utils


def _hold_port_with_real_queue_manager(port: int) -> None:
    """Target for a child that runs the actual queue manager."""
    mp_queue.start_queue_manager(['fake_queue'], port=port)


def test_reap_orphan_returns_false_when_port_free():
    """No reaping should happen when the port is free."""
    port = common_utils.find_free_port(50215)
    # Port is free, nothing to reap.
    # pylint: disable=protected-access
    assert queue_base._reap_orphan_queue_manager(port) is False


def test_reap_orphan_skips_non_multiprocessing_holder():
    """A bare socket listener that isn't a SkyPilot queue manager must
    NOT be killed by the orphan reaper, even if its ppid==1."""
    port = common_utils.find_free_port(50315)
    # Bind a plain TCP socket in this process. Not multiprocessing → must
    # not match the heuristic.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('127.0.0.1', port))
    sock.listen(1)
    try:
        # Even if the reaper FOUND this pid as the holder, the cmdline
        # check ('multiprocessing' in cmdline) should reject it.
        result = queue_base._reap_orphan_queue_manager(port)  # pylint: disable=protected-access
        # The current Python process is not a multiprocessing.spawn child,
        # so the reaper must refuse to kill it.
        assert result is False
        # Confirm we're still alive and the socket is still ours.
        assert sock.fileno() != -1
    finally:
        sock.close()


def test_reap_orphan_kills_only_init_parented():
    """An alive multiprocessing child whose parent is still alive must
    NOT be reaped — only orphans (ppid==1) qualify."""
    port = common_utils.find_free_port(50415)
    server = multiprocessing.Process(target=mp_queue.start_queue_manager,
                                     args=(['q'], port))
    server.start()
    try:
        # Wait for the server to be listening.
        mp_queue.wait_for_queues_to_be_ready(['q'], server, port=port)
        # The queue manager subprocess is alive AND parented to the main
        # test process (ppid != 1). The reaper must refuse to kill it.
        # pylint: disable=protected-access
        assert queue_base._reap_orphan_queue_manager(port) is False
        # Server still running.
        assert server.is_alive()
    finally:
        server.terminate()
        server.join(timeout=5)


@pytest.mark.skipif(not sys.platform.startswith('linux'),
                    reason='PR_SET_PDEATHSIG is Linux-only')
def test_queue_manager_dies_with_parent():
    """The death-pact: if the queue manager's parent dies, the kernel
    must SIGTERM the queue manager. This is what prevents the orphan in
    the first place — without it, killing the parent leaves a stale
    listener on the queue port forever."""
    port = common_utils.find_free_port(50515)
    # Spawn the queue manager via a SHELL grandparent we can kill
    # without bringing down the test runner. Strategy: parent=this test,
    # middle=forked child, grandchild=queue manager. Kill middle hard,
    # expect grandchild to receive SIGTERM via PDEATHSIG within a
    # reasonable timeout.
    ready_r, ready_w = os.pipe()
    middle_pid = os.fork()
    if middle_pid == 0:
        # Middle process: spawn the queue manager and notify parent of pid.
        try:
            os.close(ready_r)
            mgr = multiprocessing.Process(target=mp_queue.start_queue_manager,
                                          args=(['q'], port))
            mgr.start()
            os.write(ready_w, f'{mgr.pid}\n'.encode())
            os.close(ready_w)
            # Block until killed.
            while True:
                time.sleep(60)
        except Exception:  # pylint: disable=broad-except
            os._exit(1)  # pylint: disable=protected-access
        os._exit(0)  # pylint: disable=protected-access
    os.close(ready_w)
    try:
        # Read the grandchild pid that middle posted.
        buf = b''
        deadline = time.time() + 10
        while b'\n' not in buf and time.time() < deadline:
            chunk = os.read(ready_r, 64)
            if not chunk:
                break
            buf += chunk
        os.close(ready_r)
        assert b'\n' in buf, 'middle child never wrote the pid'
        grandchild_pid = int(buf.strip())

        # Sanity: the grandchild is alive.
        os.kill(grandchild_pid, 0)

        # Hard-kill the middle (parent of the queue manager). With
        # PDEATHSIG armed, the grandchild should receive SIGTERM almost
        # immediately and exit.
        os.kill(middle_pid, signal.SIGKILL)
        os.waitpid(middle_pid, 0)

        deadline = time.time() + 10
        gone = False
        while time.time() < deadline:
            try:
                os.kill(grandchild_pid, 0)
            except ProcessLookupError:
                gone = True
                break
            time.sleep(0.1)
        assert gone, (f'grandchild queue manager pid {grandchild_pid} was '
                      f'NOT killed by PDEATHSIG within 10s; PR_SET_PDEATHSIG '
                      f'is not in effect')
    finally:
        # Best-effort cleanup if assertion fired.
        try:
            os.kill(middle_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_factory_start_retries_after_reaping_orphan():
    """End-to-end: a port-conflict at start() should trigger orphan
    recovery; if the conflicting holder is reaped, start() succeeds."""
    port = common_utils.find_free_port(50615)

    # Pretend the port IS in use the first time, then free after reap.
    port_states = [False, True]  # is_port_available() returns
    orphan_kills = []

    def fake_is_port_available(p):
        del p
        return port_states.pop(0) if port_states else True

    def fake_reap(p):
        orphan_kills.append(p)
        return True

    with mock.patch.object(queue_base.common_utils, 'is_port_available',
                           side_effect=fake_is_port_available), \
         mock.patch.object(queue_base, '_reap_orphan_queue_manager',
                           side_effect=fake_reap):
        factory = queue_base.MultiprocessingQueueFactory(port=port)
        proc = factory.start()
    try:
        assert orphan_kills == [
            port
        ], ('orphan reaper should have been called exactly once with the '
            'conflicting port')
        assert proc is not None and proc.is_alive(), (
            'queue manager process should be running after reap+retry')
    finally:
        if proc is not None:
            proc.terminate()
            proc.join(timeout=5)
