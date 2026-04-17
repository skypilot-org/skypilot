"""Tests for sky.utils.slurm_converter."""
import textwrap

from sky.utils import slurm_converter


def _convert(script: str):
    return slurm_converter.convert_slurm_script(textwrap.dedent(script))


def test_basic_mapping_all_supported_directives():
    script = """\
        #!/bin/bash
        #SBATCH --job-name=train
        #SBATCH --nodes=2
        #SBATCH --gpus-per-node=h100:8
        #SBATCH --cpus-per-task=32
        #SBATCH --mem=256G

        python train.py
        """
    yaml_text, warnings = _convert(script)
    assert 'name: train' in yaml_text
    assert 'num_nodes: 2' in yaml_text
    assert 'accelerators: H100:8' in yaml_text
    assert 'cpus: 32+' in yaml_text
    assert 'memory: 256+' in yaml_text
    assert 'python train.py' in yaml_text
    assert warnings == []


def test_short_flag_forms():
    script = """\
        #!/bin/bash
        #SBATCH -J myjob
        #SBATCH -N 4
        #SBATCH -c 8
        #SBATCH -G 16

        echo hi
        """
    yaml_text, _ = _convert(script)
    assert 'name: myjob' in yaml_text
    assert 'num_nodes: 4' in yaml_text
    assert 'cpus: 8+' in yaml_text
    # -G is total GPUs; 16 / 4 = 4 per node.
    assert 'accelerators: <GPU_TYPE>:4' in yaml_text


def test_gres_gpu_spec():
    script = """\
        #!/bin/bash
        #SBATCH --nodes=1
        #SBATCH --gres=gpu:v100:4

        echo hi
        """
    yaml_text, _ = _convert(script)
    assert 'accelerators: V100:4' in yaml_text


def test_memory_units():
    for mem_spec, expected_gb in [
        ('--mem=16G', 16),
        ('--mem=1024M', 1),
        ('--mem=1024', 1),  # Slurm default is MB.
        ('--mem=2T', 2048),
    ]:
        script = f"""\
            #!/bin/bash
            #SBATCH {mem_spec}
            echo hi
            """
        yaml_text, _ = _convert(script)
        assert f'memory: {expected_gb}+' in yaml_text, mem_spec


def test_env_var_substitution():
    script = """\
        #!/bin/bash
        #SBATCH --nodes=2

        echo "nodes=$SLURM_NNODES rank=$SLURM_PROCID"
        echo "id=${SLURM_JOB_ID}"
        """
    yaml_text, _ = _convert(script)
    assert '$SKYPILOT_NUM_NODES' in yaml_text
    assert '$SKYPILOT_NODE_RANK' in yaml_text
    assert '$SKYPILOT_TASK_ID' in yaml_text
    assert 'SLURM_' not in yaml_text


def test_strip_bare_srun():
    script = """\
        #!/bin/bash
        #SBATCH --nodes=2

        srun python train.py
        """
    yaml_text, warnings = _convert(script)
    assert 'python train.py' in yaml_text
    assert 'srun python' not in yaml_text
    assert warnings == []


def test_srun_single_task_per_node_on_multi_node_job():
    """`srun --ntasks-per-node=1 cmd` on a 2-node job = run once per node."""
    script = """\
        #!/bin/bash
        #SBATCH --nodes=2

        srun --ntasks-per-node=1 python train.py
        """
    yaml_text, warnings = _convert(script)
    # Tasks-per-node==1 on a multi-node job = bare command on each node.
    assert 'python train.py' in yaml_text
    assert 'srun' not in yaml_text
    assert 'SKYPILOT_NODE_RANK' not in yaml_text
    assert warnings == []


def test_srun_single_task_gates_on_rank_zero():
    """`srun -N1 -n1 cmd` in a multi-node job runs on one node only."""
    script = """\
        #!/bin/bash
        #SBATCH --nodes=4

        srun -N1 -n1 python preprocess.py
        python train.py
        """
    yaml_text, warnings = _convert(script)
    assert 'if [ "${SKYPILOT_NODE_RANK:-0}" = "0" ]; then' in yaml_text
    assert '  python preprocess.py' in yaml_text
    assert 'fi' in yaml_text
    # The unwrapped line still runs on every node.
    assert '\n  python train.py\n' in yaml_text or '\n  python train.py' in (
        yaml_text)
    assert warnings == []


def test_srun_multi_tasks_per_node_emits_launcher_templates():
    script = """\
        #!/bin/bash
        #SBATCH --nodes=2

        srun --ntasks-per-node=8 python train.py
        """
    yaml_text, warnings = _convert(script)
    # The executable command loses the srun wrapper; only comments should
    # still mention srun (as part of the TODO note explaining why).
    code_lines = [
        line for line in yaml_text.splitlines()
        if not line.lstrip().startswith(('#', '//')) and 'srun' in line
    ]
    assert code_lines == [], code_lines
    assert 'python train.py' in yaml_text
    # Both launcher templates should be present.
    assert '--nproc_per_node=8' in yaml_text
    assert '$SKYPILOT_NODE_RANK' in yaml_text
    assert 'mpirun' in yaml_text
    assert 'ppr:8:node' in yaml_text
    assert any('torchrun' in w or 'launcher' in w for w in warnings)


def test_mpirun_gets_hostfile_template():
    script = """\
        #!/bin/bash
        #SBATCH --nodes=2

        mpirun -n 16 python app.py
        """
    yaml_text, warnings = _convert(script)
    # Original command is preserved and a commented template is inserted.
    assert 'mpirun -n 16 python app.py' in yaml_text
    assert '/tmp/hostfile' in yaml_text
    assert '$SKYPILOT_NODE_IPS' in yaml_text
    assert any('hostfile' in w.lower() for w in warnings)


def test_srun_drops_harmless_flags():
    script = """\
        #!/bin/bash
        #SBATCH --nodes=2

        srun --cpu-bind=cores --mpi=pmix --gres=gpu:1 python app.py
        """
    yaml_text, warnings = _convert(script)
    # All the flags are in the drop-list; command runs on every node.
    assert 'srun' not in yaml_text
    assert 'python app.py' in yaml_text
    assert warnings == []


def test_srun_single_node_job_keeps_command_bare():
    """On a single-node job, `srun -N1 -n1 cmd` is just `cmd`."""
    script = """\
        #!/bin/bash
        #SBATCH --nodes=1

        srun -N1 -n1 python app.py
        """
    yaml_text, _ = _convert(script)
    # No multi-node fan-out, so no rank guard needed.
    assert 'SKYPILOT_NODE_RANK' not in yaml_text
    assert 'python app.py' in yaml_text


def test_srun_unknown_flag_warns():
    script = """\
        #!/bin/bash
        #SBATCH --nodes=2

        srun --some-new-flag=foo python app.py
        """
    _, warnings = _convert(script)
    assert any('some-new-flag' in w for w in warnings)


def test_unsupported_directives_preserved_as_comments():
    """Protected / non-passthrough directives stay as review comments."""
    script = """\
        #!/bin/bash
        #SBATCH --output=out.log
        #SBATCH --array=1-10

        echo hi
        """
    yaml_text, _ = _convert(script)
    # These should not go into sbatch_options (output is protected, array
    # needs a different execution model).
    assert 'sbatch_options' not in yaml_text
    for label in ('--output=out.log', '--array=1-10'):
        assert label in yaml_text


def test_non_protected_directives_pass_through_as_sbatch_options():
    """Non-protected directives go into config.slurm.sbatch_options."""
    script = """\
        #!/bin/bash
        #SBATCH --account=myaccount
        #SBATCH --qos=high
        #SBATCH --constraint=gpu80gb
        #SBATCH --mail-user=me@example.com
        #SBATCH --mail-type=END
        #SBATCH --exclusive

        echo hi
        """
    yaml_text, _ = _convert(script)
    assert 'config:' in yaml_text
    assert '  slurm:' in yaml_text
    assert '    sbatch_options:' in yaml_text
    assert 'account: myaccount' in yaml_text
    assert 'qos: high' in yaml_text
    assert 'constraint: gpu80gb' in yaml_text
    # Values containing ':' or '@' should get quoted.
    assert "mail-user: 'me@example.com'" in yaml_text
    assert 'mail-type: END' in yaml_text
    # Flags without a value become boolean true.
    assert 'exclusive: true' in yaml_text


def test_protected_directives_not_passed_through():
    """Protected sbatch options never end up in sbatch_options."""
    script = """\
        #!/bin/bash
        #SBATCH --output=out.log
        #SBATCH --error=err.log

        echo hi
        """
    yaml_text, _ = _convert(script)
    # The converter already consumes --output/--error as comments rather than
    # sbatch_options, so config should not appear at all.
    assert 'sbatch_options' not in yaml_text


def test_time_maps_to_autostop():
    for time_spec, expected_min in [
        ('--time=24:00:00', 24 * 60),  # HH:MM:SS
        ('--time=30', 30),  # bare minutes
        ('--time=90:00', 90),  # MM:SS = 90 minutes + 0 seconds
        ('--time=1-12:00:00', 36 * 60),  # 1d 12h
        ('--time=2-00', 48 * 60),  # 2d
    ]:
        script = f"""\
            #!/bin/bash
            #SBATCH {time_spec}
            echo hi
            """
        yaml_text, _ = _convert(script)
        assert 'autostop:' in yaml_text, time_spec
        assert f'idle_minutes: {expected_min}' in yaml_text, time_spec
        assert 'down: true' in yaml_text, time_spec
        assert 'wait_for: none' in yaml_text, time_spec
        # --time should no longer appear in the unsupported-notes comments.
        assert '--time=' not in yaml_text, time_spec


def test_partition_maps_to_infra():
    script = """\
        #!/bin/bash
        #SBATCH --partition=gpu-a100
        echo hi
        """
    yaml_text, warnings = _convert(script)
    assert 'infra: slurm/<cluster>/gpu-a100' in yaml_text
    # --partition should no longer appear in the unsupported-notes comments.
    assert '--partition=' not in yaml_text
    # A warning should remind the user to fill in the cluster name.
    assert any('<cluster>' in w for w in warnings)


def test_invalid_time_emits_warning():
    script = """\
        #!/bin/bash
        #SBATCH --time=not-a-time
        echo hi
        """
    yaml_text, warnings = _convert(script)
    assert 'autostop:' not in yaml_text
    assert any('--time' in w for w in warnings)


def test_missing_gpu_type_adds_warning_and_placeholder():
    script = """\
        #!/bin/bash
        #SBATCH --gpus-per-node=4
        echo hi
        """
    yaml_text, warnings = _convert(script)
    assert 'accelerators: <GPU_TYPE>:4' in yaml_text
    assert any('GPU type' in w for w in warnings)


def test_mem_per_cpu_scales_with_cpus():
    script = """\
        #!/bin/bash
        #SBATCH --cpus-per-task=8
        #SBATCH --mem-per-cpu=4G
        echo hi
        """
    yaml_text, _ = _convert(script)
    # 8 cpus * 4GB = 32 GB
    assert 'memory: 32+' in yaml_text


def test_num_nodes_omitted_for_single_node():
    script = """\
        #!/bin/bash
        #SBATCH --nodes=1
        echo hi
        """
    yaml_text, _ = _convert(script)
    assert 'num_nodes' not in yaml_text


def test_shebang_and_blank_header_lines():
    script = """\
        #!/bin/bash

        #SBATCH --job-name=x

        # A comment before the body
        echo hi
        """
    yaml_text, _ = _convert(script)
    assert 'name: x' in yaml_text
    assert 'echo hi' in yaml_text


def test_preserves_body_after_first_real_command():
    script = """\
        #!/bin/bash
        #SBATCH --job-name=x
        module load cuda/12.1
        #SBATCH --nodes=2

        python train.py
        """
    yaml_text, _ = _convert(script)
    # The directive after the first real command should NOT take effect.
    assert 'num_nodes' not in yaml_text
    # The line is part of the body (as an inline comment).
    assert 'module load cuda/12.1' in yaml_text
