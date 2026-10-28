"""A follow-mode job log tail exits once the caller that runs it is gone."""
import os
import select
import shlex
import signal
import subprocess
import sys
import textwrap
import threading
import time

from sky.skylet import job_lib
from sky.skylet import log_lib
from sky.utils import command_runner

# Runs the generated tail code against job 1, which is RUNNING until the file
# `done` appears in the log dir and SUCCEEDED after.
_DRIVER = textwrap.dedent("""
    import os, sys
    from sky.skylet import job_lib, log_lib
    log_dir, code = sys.argv[1], sys.argv[2]
    def status(job_id=None, **kwargs):
        if os.path.exists(os.path.join(log_dir, 'done')):
            return job_lib.JobStatus.SUCCEEDED
        return job_lib.JobStatus.RUNNING
    job_lib.get_log_dir_for_job = lambda job_id: log_dir
    job_lib.update_job_status = lambda job_ids, silent=False: [status()]
    job_lib.get_status_no_lock = status
    job_lib.get_status = status
    log_lib._ORPHAN_WATCHDOG_INTERVAL_SECONDS = 0.1
    open(os.path.join(log_dir, 'started'), 'w').close()
    exec(code, {'__name__': '__main__'})
""")

_FIRST_LINES = [f'{log_lib.LOG_FILE_START_STREAMING_AT}node', 'hello']


def _follow_tail_argv(tmp_path):
    content = ''.join(f'{line}\n' for line in _FIRST_LINES)
    (tmp_path / 'run.log').write_text(content, encoding='utf-8')
    command = job_lib.JobLibCodeGen.tail_logs(job_id=1,
                                              managed_job_id=None,
                                              follow=True)
    code = shlex.split(command)[-1]
    return [sys.executable, '-u', '-c', _DRIVER, str(tmp_path), code]


def _wait_for_output(proc: subprocess.Popen, text: str) -> None:
    deadline = time.time() + 30
    out = b''
    while text.encode() not in out:
        remaining = deadline - time.time()
        assert remaining > 0, out
        readable, _, _ = select.select([proc.stdout], [], [], remaining)
        if readable:
            chunk = os.read(proc.stdout.fileno(), 4096)
            assert chunk, f'tail exited before streaming: {out!r}'
            out += chunk


def test_follow_tail_exits_when_stdin_closes(tmp_path):
    # `kubectl exec -i` gives the remote tail a stdin pipe that reaches EOF
    # when the exec session goes away; nothing else tells the tail.
    proc = subprocess.Popen(_follow_tail_argv(tmp_path),
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    try:
        _wait_for_output(proc, 'hello')
        proc.stdin.close()
        assert proc.wait(timeout=10) == -signal.SIGTERM
    finally:
        proc.kill()
        proc.wait()


def test_follow_tail_with_stdin_at_eof_runs_to_the_end(tmp_path):
    # The local runner gives the tail stdin=/dev/null, which is at EOF from the
    # start; the follow must still deliver every line and finish normally.
    argv = _follow_tail_argv(tmp_path)

    def _finish_job_while_following():
        deadline = time.time() + 30
        while not (tmp_path / 'started').exists() and time.time() < deadline:
            time.sleep(0.05)
        time.sleep(1)
        with open(tmp_path / 'run.log', 'a', encoding='utf-8') as f:
            f.write('world\n')
        (tmp_path / 'done').touch()

    finisher = threading.Thread(target=_finish_job_while_following, daemon=True)
    finisher.start()
    returncode, stdout, _ = command_runner.LocalProcessCommandRunner().run(
        shlex.join(argv), require_outputs=True, stream_logs=False)
    finisher.join()
    assert returncode == 0, stdout
    expected = _FIRST_LINES + ['world']
    lines = stdout.splitlines()
    assert [line for line in lines if line in expected] == expected, stdout
    finished = lines[lines.index('world') + 1:]
    assert any('Job finished (status: SUCCEEDED).' in line
               for line in finished), stdout
