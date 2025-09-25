"""Distributed training example with PyTorch.

Usage:
    python train.py
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
        # Explicit check for torchrun
        'if ! command -v torchrun >/dev/null 2>&1; then',
        'echo "ERROR: torchrun command not found" >&2'
        'exit 1',
        'fi',
        ('torchrun '
         '--nnodes=$SKYPILOT_NUM_NODES '
         '--nproc_per_node=$SKYPILOT_NUM_GPUS_PER_NODE '
         '--master_addr=$MASTER_ADDR '
         '--master_port=8008 '
         '--node_rank=${SKYPILOT_NODE_RANK} '
         'main.py'),
    ],
)

cluster_name = 'train'
req = sky.launch(task, cluster_name=cluster_name)
job_id, _ = sky.stream_and_get(req)
sky.tail_logs(cluster_name, job_id, follow=True)

print('Training completed. Downloading checkpoint...')
subprocess.run(
    (f'scp {cluster_name}:~/sky_workdir/examples/mingpt/gpt_snapshot.pt '
     'gpt_snapshot.pt'),
    shell=True,
    check=True)
print('Checkpoint downloaded.')

print(f'Tearing down cluster {cluster_name}...')
req = sky.down(cluster_name)
sky.stream_and_get(req)
print(f'Cluster {cluster_name} torn down.')
