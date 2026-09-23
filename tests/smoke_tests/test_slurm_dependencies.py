"""Slurm dependencies between SkyPilot cluster allocations."""
import os
import subprocess
import sys
import time

import pytest
from smoke_tests import smoke_tests_utils
import yaml

import sky
from sky.provision.slurm import utils as slurm_utils


@pytest.mark.slurm
@pytest.mark.no_remote_server
@pytest.mark.parametrize('num_nodes', [1, 2])
@pytest.mark.parametrize('exit_code', [0, 7])
def test_slurm_afterok(tmp_path, num_nodes: int, exit_code: int):
    """The second sky launch depends on the first allocation's task result."""
    name = smoke_tests_utils.get_cluster_name()
    first, second = f'{name}-a', f'{name}-b'
    cluster = os.environ.get('SLURM_CLUSTER')
    if cluster is None:
        clusters = slurm_utils.get_all_slurm_cluster_names()
        if len(clusters) != 1:
            pytest.skip(
                'Set SLURM_CLUSTER when multiple clusters are configured.')
        cluster = clusters[0]
    cli = [sys.executable, '-c', 'from sky.cli import cli; cli()']
    ssh = [
        'ssh', '-F',
        os.path.expanduser(slurm_utils.DEFAULT_SLURM_PATH), '-o',
        'BatchMode=yes', cluster
    ]

    def run(args):
        result = subprocess.run(args,
                                capture_output=True,
                                text=True,
                                timeout=600,
                                check=False)
        print(result.stdout, result.stderr, flush=True)
        return result

    def queue():
        result = run(ssh + ['squeue --noheader --format="%i|%T|%r|%E|%j"'])
        assert result.returncode == 0
        return [line.split('|') for line in result.stdout.splitlines()]

    launch = None
    try:
        # Keep the completed task's allocation alive until its dependent is
        # confirmed pending, so admission timing cannot bypass the assertion.
        result = run(cli + [
            'launch', '-y', '-c', first, '--infra', f'slurm/{cluster}',
            '--cpus', '1', '--num-nodes',
            str(num_nodes), '--', f'exit {exit_code}'
        ])
        assert result.returncode == (0 if exit_code == 0 else 100)
        records = sky.get(sky.status(cluster_names=[first]))
        assert len(records) == 1
        cloud_name = records[0]['handle'].cluster_name_on_cloud
        allocations = [row[0] for row in queue() if row[4] == cloud_name]
        assert len(allocations) == 1, allocations
        allocation = allocations[0]
        assert allocation.isdigit(), allocation
        task = tmp_path / 'dependent.yaml'
        task.write_text(
            yaml.safe_dump({
                'resources': {
                    'infra': f'slurm/{cluster}',
                    'cpus': 1
                },
                'config': {
                    'slurm': {
                        'sbatch_options': {
                            'dependency': f'afterok:{allocation}',
                            'kill-on-invalid-dep': 'yes',
                        }
                    }
                },
                'run': 'echo AFTEROK_DEPENDENT_RAN',
            }))
        with (tmp_path / 'dependent.log').open('w+') as log:
            launch = subprocess.Popen(cli +
                                      ['launch', '-y', '-c', second,
                                       str(task)],
                                      stdout=log,
                                      stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 180
            while True:
                assert launch.poll() is None, 'Dependent launch exited early'
                dependents = [
                    row for row in queue()
                    if row[3].split('(')[0] == f'afterok:{allocation}'
                ]
                if dependents:
                    assert len(dependents) == 1, dependents
                    dependent = dependents[0]
                    assert dependent[1:3] == ['PENDING',
                                              'Dependency'], dependent
                    break
                assert time.monotonic(
                ) < deadline, 'Dependent was not submitted'
                time.sleep(2)
            result = run(cli + ['down', '-y', first])
            assert result.returncode == 0
            launch.wait(timeout=600)
            log.seek(0)
            output = log.read()
            print(output, flush=True)
            if exit_code == 0:
                assert launch.returncode == 0, output
                result = run(cli + ['logs', second, '1', '--no-follow'])
                assert result.returncode == 0
                assert 'AFTEROK_DEPENDENT_RAN' in result.stdout
            else:
                assert launch.returncode != 0, output
                # Accounting retains the cancelled allocation after it leaves
                # the active queue. It must never have started executing.
                result = run(ssh + [
                    f'sacct -X -n -P -j {dependent[0]} '
                    '--format=JobIDRaw,State,Start'
                ])
                assert result.returncode == 0
                rows = [line.split('|') for line in result.stdout.splitlines()]
                assert len(rows) == 1, rows
                assert rows[0][:2] == [dependent[0], 'CANCELLED'], rows
                assert rows[0][2] in ('None', 'Unknown'), rows
    finally:
        if launch is not None and launch.poll() is None:
            launch.terminate()
            launch.wait(timeout=30)
        result = run(cli + ['down', '-y', first, second])
        assert result.returncode == 0
