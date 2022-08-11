import os
from typing import List, Optional

import sky

with sky.Dag() as dag:
    # Total Nodes, INCLUDING Head Node
    num_nodes = 2

    workdir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'ray_tune_examples')

    # The setup command.  Will be run under the working directory.
    setup = 'pip3 install --upgrade pip && \
        pip3 install ray[tune] pytorch-lightning==1.4.9 lightning-bolts torchvision'

    # head_run = 'python3 tune_basic_example.py --smoke-test'
    head_run = 'python3 tune_ptl_example.py'

    # The command to run.  Will be run under the working directory.
    def run_fn(node_rank: int, ip_list: List[str]) -> Optional[str]:
        return head_run if node_rank == 0 else None

    train = sky.Task(
        'train',
        workdir=workdir,
        setup=setup,
        num_nodes=num_nodes,
        run=run_fn,
    )

    train.set_resources({
        sky.Resources(sky.AWS(), 'p3.2xlarge'),
    })

sky.launch(dag)
