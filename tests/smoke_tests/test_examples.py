import os
import re
import tempfile
from typing import Dict

import pytest
from smoke_tests import smoke_tests_utils

from sky.skylet import constants


# ---------- Test min-gpt ----------
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet
@pytest.mark.no_hyperbolic  # Hyperbolic not support num_nodes > 1 yet
@pytest.mark.no_seeweb  # Seeweb does not support multi-node
@pytest.mark.resource_heavy
@pytest.mark.parametrize('train_file', [
    'examples/distributed-pytorch/train.yaml',
    'examples/distributed-pytorch/train-rdzv.yaml'
])
@pytest.mark.parametrize('accelerator', [{'do': 'H100', 'nebius': 'L40S'}])
def test_min_gpt(generic_cloud: str, train_file: str, accelerator: Dict[str,
                                                                        str]):
    if generic_cloud == 'kubernetes':
        accelerator = smoke_tests_utils.get_avaliabe_gpus_for_k8s_tests()
    else:
        accelerator = accelerator.get(generic_cloud, 'T4')
    name = smoke_tests_utils.get_cluster_name()

    def read_and_modify(file_path: str) -> str:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        # Let the train exit after 1 epoch
        modified_content = content.replace(
            'main.py', 'main.py trainer_config.max_epochs=1')
        modified_content = re.sub(r'accelerators:\s*[^\n]+',
                                  f'accelerators: {accelerator}',
                                  modified_content)
        # MinGPT code has a hardcoded 2 GPUs per node check, we need to set it to a fork that removes
        # the check
        modified_content = re.sub(
            r'git clone .*',
            'git clone --depth 1 -b fix-mingpt https://github.com/michaelvll/examples || true',
            modified_content)

        # Create a temporary YAML file with the modified content
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            f.write(modified_content)
            f.flush()
            train_file_path = f.name
        return train_file_path

    dist_train_file = read_and_modify(train_file)

    test = smoke_tests_utils.Test(
        'min_gpt',
        [
            f'sky launch -y -c {name} --infra {generic_cloud} {dist_train_file}',
            f'sky logs {name} 1 --status',
            f'outputs=$(sky logs {name} 1); echo "$outputs" && echo "$outputs" | grep "Epoch 1 | Iter 0 | Train Loss"',
        ],
        f'sky down -y {name}; rm {dist_train_file}',
        timeout=20 * 60,
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Test ray-train ----------
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet
@pytest.mark.no_hyperbolic  # Hyperbolic not support num_nodes > 1 yet
@pytest.mark.no_seeweb  # Seeweb does not support multi-node
@pytest.mark.resource_heavy
@pytest.mark.parametrize('accelerator', [{'do': 'H100', 'nebius': 'L40S'}])
def test_ray_train(generic_cloud: str, accelerator: Dict[str, str]) -> None:
    if generic_cloud == 'kubernetes':
        accelerator = smoke_tests_utils.get_avaliabe_gpus_for_k8s_tests()
    else:
        accelerator = accelerator.get(generic_cloud, 'T4')
    name = smoke_tests_utils.get_cluster_name()

    with open('examples/distributed_ray_train/train.py', 'r',
              encoding='utf-8') as f:
        content = f.read()
    # Let the train exit after 1 epoch
    modified_content = content.replace('\'epochs\': 10,', '\'epochs\': 1,')

    # Create a temporary YAML file with the modified content
    with tempfile.TemporaryDirectory(
            suffix='ray_train_tmp_workdir') as temp_dir:
        train_file_path = os.path.join(temp_dir, 'train.py')
        with open(train_file_path, 'w', encoding='utf-8') as f:
            f.write(modified_content)
            f.flush()
        workdir_dir = temp_dir

        with open('examples/distributed_ray_train/ray_train.yaml',
                  'r',
                  encoding='utf-8') as f:
            content = f.read()
        # Let the train exit after 1 epoch
        modified_content = content.replace('workdir: .',
                                           f'workdir: {workdir_dir}')

        # Create a temporary YAML file with the modified content
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            f.write(modified_content)
            f.flush()
            yaml_file_path = f.name

        test = smoke_tests_utils.Test(
            'ray_train',
            [
                f'sky launch -y -c {name} --infra {generic_cloud} --memory 8+ --gpus {accelerator} {yaml_file_path}',
                f'sky logs {name} 1 --status',
                f'outputs=$(sky logs {name} 1); echo "$outputs" && echo "$outputs" | grep "Train Epoch 0:"',
            ],
            f'sky down -y {name}; rm -r {workdir_dir}; rm {yaml_file_path}',
            timeout=20 * 60,
        )
        smoke_tests_utils.run_one_test(test)


# ---------- Test NeMo RL ----------
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet
@pytest.mark.no_hyperbolic  # Hyperbolic not support num_nodes > 1 yet
@pytest.mark.no_seeweb  # Seeweb does not support multi-node
@pytest.mark.resource_heavy
@pytest.mark.parametrize('accelerator', [{
    'do': 'H100',
    'nebius': 'L40S',
    'aws': 'L4',
    'gcp': 'L4'
}])
def test_nemorl(generic_cloud: str, accelerator: Dict[str, str]) -> None:
    cpu = '10+'
    memory = '60+'
    if generic_cloud == 'kubernetes':
        accelerator = smoke_tests_utils.get_avaliabe_gpus_for_k8s_tests()
    else:
        accelerator = accelerator.get(generic_cloud, 'T4')

    infra = generic_cloud
    if generic_cloud == 'aws':
        infra = 'aws/ap-northeast-1'

    name = smoke_tests_utils.get_cluster_name()
    original_yaml_path = 'llm/nemorl/nemorl.sky.yaml'

    with open(original_yaml_path, 'r') as f:
        content = f.read()

    modified_content = re.sub(
        r'(?m)^(\s*)dpo\.val_global_batch_size=.*\\\s*$',
        r'\1dpo.val_global_batch_size=1 \\\n\1dpo.max_num_steps=1 \\\n\1policy.model_name="Qwen/Qwen3-0.6B" \\\n\1policy.tokenizer.name="Qwen/Qwen3-0.6B" \\',
        content,
    )

    # Create a temporary YAML file with the modified content
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml') as f:
        f.write(modified_content)
        f.flush()

        test = smoke_tests_utils.Test(
            'nemorl',
            [
                f'HF_TOKEN="" sky launch -y -c {name} --infra {infra} --gpus {accelerator} --cpus {cpu} --memory {memory} --secret HF_TOKEN {f.name}',
                f'sky logs {name} 1 --status',
            ],
            f'sky down -y {name}',
            timeout=30 * 60,
        )
        smoke_tests_utils.run_one_test(test)
