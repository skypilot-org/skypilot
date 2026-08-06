"""Unit tests for the v1 sbatch script's SkyPilot runtime env vars.

The legacy executor (``sky/skylet/executor/slurm.py``) provides
``SKYPILOT_NODE_RANK`` / ``SKYPILOT_NUM_NODES`` / ``SKYPILOT_NODE_IPS``
/ ``SKYPILOT_NUM_GPUS_PER_NODE`` to user code; the v1 sbatch script must
provide the same contract.
"""
# pylint: disable=protected-access,missing-class-docstring
from sky.provision.slurm import instance as slurm_instance
from sky.skylet import constants


def _build_script(num_nodes=2,
                  accelerator_count=1,
                  envs=None,
                  setup='pip install -e .',
                  run='python train.py'):
    return slurm_instance._build_v1_sbatch_script(
        cluster_name_on_cloud='test-cluster',
        num_nodes=num_nodes,
        log_path='/home/u/.sky_provision/slurm-%j.out',
        resources={
            'cpus': 4,
            'memory': 8,
            'accelerator_type': 'gh200',
            'accelerator_count': accelerator_count,
        },
        setup=setup,
        run=run,
        envs=envs or {},
        workdir=None,
        file_mounts=None,
        container_image=None,
        extra_sbatch_directives='',
    )


class TestRuntimeEnvVars:

    def test_allocation_wide_vars_in_preamble(self):
        script = _build_script(num_nodes=2, accelerator_count=1)
        preamble = script.split('srun ', 1)[0]
        assert f'export {constants.SKYPILOT_NUM_NODES}=2' in preamble
        assert (f'export {constants.SKYPILOT_NUM_GPUS_PER_NODE}=1'
                in preamble)
        assert (f'export {constants.SKYPILOT_SETUP_NUM_GPUS_PER_NODE}=1'
                in preamble)
        # NODE_IPS is computed from the allocation's nodelist and
        # exported for the srun tasks to inherit.
        assert 'scontrol show hostnames "$SLURM_JOB_NODELIST"' in preamble
        assert f'export {constants.SKYPILOT_NODE_IPS}' in preamble

    def test_node_rank_derived_inside_srun_body(self):
        """Rank is per-task, so the export must live inside the srun
        body (evaluated per task), not the preamble (evaluated once)."""
        script = _build_script()
        rank_export = (f'export {constants.SKYPILOT_NODE_RANK}='
                       '"$SLURM_PROCID"')
        preamble, srun_body = script.split('srun ', 1)
        assert rank_export not in preamble
        assert rank_export in srun_body

    def test_no_gpu_task_exports_zero(self):
        script = _build_script(accelerator_count=0)
        assert f'export {constants.SKYPILOT_NUM_GPUS_PER_NODE}=0' in script

    def test_rank_export_present_without_setup_or_run(self):
        script = _build_script(setup=None, run=None)
        assert f'export {constants.SKYPILOT_NODE_RANK}=' in script

    def test_user_env_overrides_runtime_var(self):
        """User envs are exported after the runtime vars so a colliding
        user-defined value wins."""
        script = _build_script(envs={constants.SKYPILOT_NUM_NODES: '99'})
        runtime_idx = script.index(f'export {constants.SKYPILOT_NUM_NODES}=2')
        user_idx = script.index(f'export {constants.SKYPILOT_NUM_NODES}=99')
        assert runtime_idx < user_idx
