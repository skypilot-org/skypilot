"""Tests for backend_utils.open_ssh_tunnel."""
import collections
import os
import pathlib
import queue
import shlex
import signal
import subprocess
import sys
import textwrap
import time
from unittest import mock

import pytest

from sky import exceptions
from sky.backends import backend_utils
from sky.utils import command_runner

# Far more than a pipe buffer (64 KiB on Linux, 16-64 KiB on macOS).
_FLOOD_LINES = 200_000

_TUNNEL_SCRIPT = textwrap.dedent("""\
    import pathlib
    import sys
    import time

    ack, marker = sys.argv[1], sys.argv[2]
    print(ack, flush=True)
    for _ in range(%d):
        sys.stdout.write('Handling connection for 12345\\n')
        sys.stderr.write('E0101 00:00:00.000000 portforward error\\n')
    sys.stdout.flush()
    sys.stderr.flush()
    pathlib.Path(marker).touch()
    time.sleep(120)
""" % _FLOOD_LINES)


def _make_runner(runner_cls, script: pathlib.Path, ack: str,
                 marker: pathlib.Path) -> mock.MagicMock:
    runner = mock.MagicMock(spec=runner_cls)
    runner.port_forward_command.return_value = [
        shlex.quote(sys.executable),
        shlex.quote(str(script)),
        shlex.quote(ack),
        shlex.quote(str(marker)),
    ]
    # Remote port check on the Kubernetes path.
    runner.run.return_value = (0, '', '')
    return runner


@pytest.mark.parametrize('runner_cls,ack', [
    (command_runner.KubernetesCommandRunner,
     'Forwarding from 127.0.0.1:12345 -> 46590'),
    (command_runner.SSHCommandRunner, 'ack'),
])
def test_tunnel_output_does_not_block_process(tmp_path, runner_cls, ack):
    """The tunnel process keeps running while writing far more than a pipe
    buffer to stdout and stderr after its ack line."""
    script = tmp_path / 'tunnel.py'
    script.write_text(_TUNNEL_SCRIPT)
    marker = tmp_path / 'flooded'
    runner = _make_runner(runner_cls, script, ack, marker)

    proc = backend_utils.open_ssh_tunnel(runner, (12345, 46590))
    try:
        deadline = time.time() + 10
        while not marker.exists() and time.time() < deadline:
            assert proc.poll() is None, 'tunnel process exited early'
            time.sleep(0.1)
        assert marker.exists(), (
            'tunnel process blocked writing its output after the ack')
        assert proc.poll() is None
    finally:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


def test_tunnel_failure_reports_output(tmp_path):
    """A tunnel process that exits before the ack raises with its stderr."""
    runner = mock.MagicMock(spec=command_runner.KubernetesCommandRunner)
    runner.port_forward_command.return_value = [
        'echo some-stdout;', 'echo pods-not-found >&2;', 'exit 1'
    ]
    with pytest.raises(exceptions.CommandError) as exc_info:
        backend_utils.open_ssh_tunnel(runner, (12345, 46590))
    err = exc_info.value
    assert err.returncode == 1
    assert 'some-stdout' in err.error_msg
    assert 'pods-not-found' in err.detailed_reason
    runner.run.assert_not_called()


def test_drain_tunnel_pipe_keeps_first_line_and_tail():
    proc = subprocess.Popen(
        [sys.executable, '-c', 'for i in range(500): print(i)'],
        stdout=subprocess.PIPE,
        text=True)
    first_line: queue.Queue = queue.Queue()
    tail: collections.deque = collections.deque(maxlen=3)
    backend_utils._drain_tunnel_pipe(proc.stdout, tail, first_line)
    proc.wait()
    assert first_line.get_nowait() == '0\n'
    assert first_line.empty()
    assert list(tail) == ['497\n', '498\n', '499\n']
