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


def test_srun_with_flags_emits_warning():
    script = """\
        #!/bin/bash
        #SBATCH --nodes=2

        srun --ntasks-per-node=1 python train.py
        """
    yaml_text, warnings = _convert(script)
    # The line should be preserved unchanged.
    assert 'srun --ntasks-per-node=1 python train.py' in yaml_text
    assert any('srun' in w for w in warnings)


def test_unsupported_directives_preserved_as_comments():
    script = """\
        #!/bin/bash
        #SBATCH --time=24:00:00
        #SBATCH --partition=gpu
        #SBATCH --account=myaccount
        #SBATCH --output=out.log
        #SBATCH --array=1-10

        echo hi
        """
    yaml_text, _ = _convert(script)
    for label in ('--time=24:00:00', '--partition=gpu', '--account=myaccount',
                  '--output=out.log', '--array=1-10'):
        assert label in yaml_text


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
