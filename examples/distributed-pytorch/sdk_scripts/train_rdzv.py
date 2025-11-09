"""Distributed training example with PyTorch using `rdzv` backend.

Usage:
    python train_rdzv.py
"""

import subprocess

import sky

task = sky.Task(
    name='minGPT-ddp',
    resources=sky.Resources(
        cpus='4+',
        accelerators='L4:2',
    ),
    num_nodes=2,
    setup=[
        'git clone --depth 1 https://github.com/pytorch/examples || true',
        'cd examples',
        ('git filter-branch --prune-empty '
         '--subdirectory-filter distributed/minGPT-ddp'),
        'uv venv --python 3.10',
        'source .venv/bin/activate',
        ('uv pip install -r requirements.txt "numpy<2" "torch==2.7.1+cu118" '
         '--extra-index-url https://download.pytorch.org/whl/cu118'),
    ],
    run=[
        'cd examples',
        'source .venv/bin/activate',
        'cd mingpt',
        'export LOGLEVEL=INFO',
        'MASTER_ADDR=$(echo "$SKYPILOT_NODE_IPS" | head -n1)',
        'echo "Starting distributed training, head node: $MASTER_ADDR"',
        ('torchrun '
         '--nnodes=$SKYPILOT_NUM_NODES '
         '--nproc_per_node=$SKYPILOT_NUM_GPUS_PER_NODE '
         '--rdzv_backend=c10d '
         '--rdzv_endpoint=$MASTER_ADDR:29500 '
         '--rdzv_id $SKYPILOT_TASK_ID '
         'main.py'),
    ],
)

# Alternatively, load in the cluster YAML from a file
# task = sky.Task.from_yaml('../train_rdzv.yaml')

cluster_name = 'train-rdzv'
job_id, _ = sky.stream_and_get(sky.launch(task, cluster_name=cluster_name))
sky.tail_logs(cluster_name, job_id, follow=True)

print('Training completed. Downloading checkpoint...')
subprocess.run(
    (f'scp {cluster_name}:~/sky_workdir/examples/mingpt/gpt_snapshot.pt '
     'gpt_snapshot.pt'),
    shell=True,
    check=True)
print('Checkpoint downloaded.')

print(f'Tearing down cluster {cluster_name}...')
sky.stream_and_get(sky.down(cluster_name))
print(f'Cluster {cluster_name} torn down.')
