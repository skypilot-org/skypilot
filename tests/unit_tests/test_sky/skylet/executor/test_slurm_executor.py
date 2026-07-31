"""Tests for the Slurm task executor."""
# pylint: disable=protected-access
import errno
import os
import subprocess
import threading

from sky.skylet.executor import slurm

# Step/task-scoped variables Slurm sets for the executor's own job step. A
# nested srun in the user script reads several of these as input defaults, so
# they must not leak into the user script's environment.
_STEP_SCOPED_VARS = {
    'SLURM_CPU_BIND': 'quiet,mask_cpu:0x3',
    'SLURM_CPU_BIND_LIST': '0x3',
    'SLURM_CPU_BIND_TYPE': 'mask_cpu:',
    'SLURM_CPU_BIND_VERBOSE': 'quiet',
    'SLURM_CPUS_PER_TASK': '1',
    'SLURM_TRES_PER_TASK': 'cpu=1',
    'SLURM_CPUS_ON_NODE': '2',
    'SLURM_NTASKS': '2',
    'SLURM_NPROCS': '2',
    'SLURM_NTASKS_PER_NODE': '1',
    'SLURM_TASKS_PER_NODE': '1(x2)',
    'SLURM_STEP_ID': '12',
    'SLURM_STEPID': '12',
    'SLURM_STEP_NUM_TASKS': '2',
    'SLURM_STEP_NODELIST': 'node[1-2]',
    'SLURM_SRUN_COMM_HOST': '10.0.0.1',
    'SLURM_SRUN_COMM_PORT': '33809',
    'SLURM_LAUNCH_NODE_IPADDR': '10.0.0.1',
    'SLURM_TASK_PID': '4242',
    'SLURM_PROCID': '0',
    'SLURM_LOCALID': '0',
    'SLURM_NODEID': '0',
    'SLURM_GTIDS': '0',
}

# Job-scoped variables that user scripts legitimately rely on (e.g.
# `srun --overlap --jobid=$SLURM_JOB_ID`) and must survive the unset.
_JOB_SCOPED_VARS = {
    'SLURM_JOB_ID': '18',
    'SLURM_JOBID': '18',
    'SLURM_JOB_NODELIST': 'node[1-2]',
    'SLURM_NODELIST': 'node[1-2]',
    'SLURM_JOB_NUM_NODES': '2',
    'SLURM_NNODES': '2',
    'SLURM_JOB_CPUS_PER_NODE': '48(x2)',
    'SLURM_GPUS_ON_NODE': '4',
    'SLURM_GPUS_PER_NODE': '4',
    'SLURM_MEM_PER_NODE': '409600',
    'SLURM_JOB_PARTITION': 'all',
    'SLURM_CONF': '/etc/slurm/slurm.conf',
}


def test_unset_step_scoped_slurm_env():
    """Run the unset snippet in bash and check what survives."""
    probe = '\n'.join(f'echo "{name}=${{{name}:-UNSET}}"'
                      for name in list(_STEP_SCOPED_VARS) +
                      list(_JOB_SCOPED_VARS))
    script = slurm.UNSET_STEP_SCOPED_SLURM_ENV + '\n' + probe
    env = {**_STEP_SCOPED_VARS, **_JOB_SCOPED_VARS, 'PATH': '/usr/bin:/bin'}
    out = subprocess.run(['/bin/bash', '-c', script],
                         env=env,
                         capture_output=True,
                         text=True,
                         check=True).stdout
    result = dict(
        line.split('=', maxsplit=1) for line in out.strip().splitlines())
    for name in _STEP_SCOPED_VARS:
        assert result[name] == 'UNSET', f'{name} leaked into user script env'
    for name, value in _JOB_SCOPED_VARS.items():
        assert result[name] == value, f'{name} was wrongly unset'


def _fail_reads_under(monkeypatch, run_done_dir, exc_factory, num_failures):
    """Makes the first num_failures reads under run_done_dir raise.

    Returns a one-element list holding how many injected failures are left, so
    callers can assert the injection actually fired.
    """
    real_open = os.open
    remaining = [num_failures]

    def fake_open(path, *args, **kwargs):
        if str(path).startswith(str(run_done_dir)) and remaining[0] > 0:
            remaining[0] -= 1
            raise exc_factory()
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, 'open', fake_open)
    return remaining


def test_wait_for_all_ranks_returns_once_every_rank_is_done(tmp_path):
    run_done_dir = tmp_path / 'run_done'
    run_done_dir.mkdir()
    for peer in range(4):
        (run_done_dir / str(peer)).touch()

    slurm._wait_for_all_ranks(str(run_done_dir), rank=0, num_nodes=4)


def test_wait_for_all_ranks_waits_for_a_late_rank(tmp_path):
    run_done_dir = tmp_path / 'run_done'
    run_done_dir.mkdir()
    (run_done_dir / '0').touch()
    (run_done_dir / '1').touch()

    # Rank 2 finishes well after the others, which is normal: the barrier has
    # no business bounding how long a peer's workload takes.
    late = threading.Timer(1.0, (run_done_dir / '2').touch)
    late.start()
    try:
        slurm._wait_for_all_ranks(str(run_done_dir), rank=1, num_nodes=3)
    finally:
        late.cancel()

    assert (run_done_dir / '2').exists()


def test_wait_for_all_ranks_survives_a_transient_error(tmp_path, monkeypatch,
                                                       capsys):
    run_done_dir = tmp_path / 'run_done'
    run_done_dir.mkdir()
    for peer in range(2):
        (run_done_dir / str(peer)).touch()
    remaining = _fail_reads_under(
        monkeypatch,
        run_done_dir,
        lambda: OSError(errno.ESTALE, 'Stale file handle'),
        num_failures=1)
    # Even with no error budget at all, one bad read followed by a good one
    # must not end the wait: the error clock only runs while reads keep
    # failing.
    monkeypatch.setattr(slurm, 'BARRIER_ERROR_TIMEOUT_SECONDS', 0)

    slurm._wait_for_all_ranks(str(run_done_dir), rank=0, num_nodes=2)

    assert remaining == [0], 'the injected error never fired'
    assert 'Gave up waiting' not in capsys.readouterr().err


def test_wait_for_all_ranks_gives_up_if_the_directory_is_removed(
        tmp_path, monkeypatch, capsys):
    # A rank that removed the directory on its way out is exactly the race
    # this barrier must not have, so nothing here removes it. If something
    # else does, the wait has to end rather than hang forever.
    run_done_dir = tmp_path / 'run_done'
    monkeypatch.setattr(slurm, 'BARRIER_ERROR_TIMEOUT_SECONDS', 0)

    slurm._wait_for_all_ranks(str(run_done_dir), rank=0, num_nodes=2)

    assert 'Gave up waiting for rank(s) [1]' in capsys.readouterr().err


def test_wait_for_all_ranks_gives_up_without_raising(tmp_path, monkeypatch,
                                                     capsys):
    run_done_dir = tmp_path / 'run_done'
    run_done_dir.mkdir()
    (run_done_dir / '1').touch()
    remaining = _fail_reads_under(
        monkeypatch,
        run_done_dir,
        lambda: OSError(errno.ESTALE, 'Stale file handle'),
        num_failures=1000)
    monkeypatch.setattr(slurm, 'BARRIER_ERROR_TIMEOUT_SECONDS', 0)

    # A barrier that cannot read the shared filesystem must not turn a
    # successful user script into a failed job.
    slurm._wait_for_all_ranks(str(run_done_dir), rank=0, num_nodes=2)

    assert remaining[0] < 1000, 'the injected error never fired'
    assert 'Gave up waiting for rank(s) [1]' in capsys.readouterr().err
