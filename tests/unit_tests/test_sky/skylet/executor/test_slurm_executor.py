"""Tests for the Slurm task executor."""
import subprocess

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
