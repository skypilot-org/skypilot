"""Tests for the PBS task executor and its resource allocator."""
# pylint: disable=protected-access
import json
import os
import subprocess
import sys
import threading
import time

import pytest

from sky.skylet.executor import pbs


class TestTaskResourceSlot:

    def _slot(self, tmp_path, job_id, num_nodes, cpus, gpus, cap=None):
        return pbs.TaskResourceSlot(job_id=job_id,
                                    num_nodes=num_nodes,
                                    cpus_per_node=cpus,
                                    gpus_per_node=gpus,
                                    node_capacity=cap or {
                                        'CPU': 8,
                                        'GPU': 4
                                    },
                                    state_path=str(tmp_path / 'slots.json'))

    def test_gpu_ids_are_disjoint_across_jobs(self, tmp_path):
        first = self._slot(tmp_path, 1, 2, 2, 2)
        assert first.acquire() == [[0, 1], [0, 1]]
        second = self._slot(tmp_path, 2, 1, 2, 2)
        assert second.acquire() == [[2, 3]]
        third = self._slot(tmp_path, 3, 1, 1, 1)
        assert third.try_acquire() is None
        second.release()
        assert third.try_acquire() == [[2]]
        first.release()
        third.release()
        assert json.loads((tmp_path / 'slots.json').read_text()) == {}

    def test_cpu_capacity(self, tmp_path):
        first = self._slot(tmp_path, 1, 1, 6, 0)
        assert first.acquire() == [[]]
        second = self._slot(tmp_path, 2, 1, 4, 0)
        assert second.try_acquire() is None
        small = self._slot(tmp_path, 3, 1, 2, 0)
        assert small.try_acquire() == [[]]

    def test_unknown_cpu_capacity_is_not_enforced(self, tmp_path):
        cap = {'CPU': 0, 'GPU': 0}
        assert self._slot(tmp_path, 1, 1, 100, 0, cap).try_acquire() == [[]]
        assert self._slot(tmp_path, 2, 1, 100, 0, cap).try_acquire() == [[]]

    def test_dead_driver_reservation_is_dropped(self, tmp_path):
        state_path = tmp_path / 'slots.json'
        state_path.write_text(
            json.dumps({
                '9': {
                    'pid': 2**22 + 12345,  # not a live pid
                    'num_nodes': 1,
                    'cpus': 8,
                    'gpu_ids': [[0, 1, 2, 3]],
                }
            }))
        slot = self._slot(tmp_path, 1, 1, 8, 4)
        assert slot.try_acquire() == [[0, 1, 2, 3]]

    def test_acquire_waits(self, tmp_path):
        holder = self._slot(tmp_path, 1, 1, 0, 4)
        holder.acquire()
        waiter = self._slot(tmp_path, 2, 1, 0, 1)
        result = {}

        def _wait():
            result['ids'] = waiter.acquire(poll_interval=0.05)

        thread = threading.Thread(target=_wait)
        thread.start()
        time.sleep(0.2)
        assert 'ids' not in result
        holder.release()
        thread.join(timeout=5)
        assert result['ids'] == [[0]]


class TestExecutorMain:

    def _run(self, tmp_path, extra_args, script):
        log_dir = tmp_path / 'logs'
        cmd = [
            sys.executable, '-m', 'sky.skylet.executor.pbs', '--rank=0',
            '--num-nodes=1', '--node-ips=10.0.0.1',
            f'--log-dir={log_dir}', f'--script={script}',
            f'--pid-file={tmp_path / "rank0.pid"}'
        ] + extra_args
        env = dict(os.environ)
        env['SKY_RUNTIME_DIR'] = str(tmp_path)
        return subprocess.run(cmd,
                              capture_output=True,
                              text=True,
                              env=env,
                              check=False), log_dir

    def test_runs_script_with_env(self, tmp_path):
        result, log_dir = self._run(
            tmp_path, ['--task-name=mytask',
                       '--env-vars=' + json.dumps({'FOO': 'bar'})],
            'echo "foo=$FOO rank=$SKYPILOT_NODE_RANK nodes=$SKYPILOT_NUM_NODES '
            'ips=$SKYPILOT_NODE_IPS"; exit 3')
        assert result.returncode == 3, result.stderr
        assert 'foo=bar rank=0 nodes=1 ips=10.0.0.1' in result.stdout
        assert '(mytask, pid=' in result.stdout
        assert (log_dir / 'run.log').exists()
        assert (tmp_path / 'rank0.pid').read_text().strip().isdigit()

    def test_setup_log_name_and_gpu_ids(self, tmp_path):
        result, log_dir = self._run(
            tmp_path, ['--is-setup', '--gpu-ids=2,3'],
            'echo "cuda=$CUDA_VISIBLE_DEVICES rank=${SKYPILOT_NODE_RANK:-unset}"')
        assert result.returncode == 0, result.stderr
        assert 'cuda=2,3 rank=unset' in result.stdout
        assert (log_dir / 'setup-head.log').exists()

    def test_multi_node_log_name(self, tmp_path):
        log_dir = tmp_path / 'logs'
        cmd = [
            sys.executable, '-m', 'sky.skylet.executor.pbs', '--rank=1',
            '--num-nodes=2', '--node-ips=10.0.0.1,10.0.0.2',
            f'--log-dir={log_dir}', '--script=echo hi'
        ]
        result = subprocess.run(cmd, capture_output=True, text=True,
                                check=False)
        assert result.returncode == 0, result.stderr
        assert '(worker1, rank=1, pid=' in result.stdout
        assert 'ip=10.0.0.2' in result.stdout
        assert (log_dir / '1-worker1.log').exists()

    @pytest.mark.timeout(60)
    def test_term_kills_children(self, tmp_path):
        log_dir = tmp_path / 'logs'
        marker = tmp_path / 'child.pid'
        cmd = [
            sys.executable, '-m', 'sky.skylet.executor.pbs', '--rank=0',
            '--num-nodes=1', '--node-ips=10.0.0.1', f'--log-dir={log_dir}',
            f'--script=echo $$ > {marker}; sleep 300',
            f'--pid-file={tmp_path / "rank0.pid"}'
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        deadline = time.time() + 20
        while not marker.exists() and time.time() < deadline:
            time.sleep(0.1)
        assert marker.exists()
        child_pid = int(marker.read_text().strip())
        # The driver signals the executor's whole process group.
        executor_pid = int((tmp_path / 'rank0.pid').read_text().strip())
        os.killpg(executor_pid, 15)
        rc = proc.wait(timeout=30)
        assert rc == pbs.CANCELLED_RETURN_CODE
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            pytest.fail('child shell survived the executor')
