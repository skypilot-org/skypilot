import os
from typing import Dict, List

import sky
from sky import clouds

IPAddr = str

with sky.Dag() as dag:
    # Total Nodes, INCLUDING Head Node
    num_nodes = 2

    workdir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'ray_tune_example')

    # The setup command.  Will be run under the working directory.
    setup = 'pip3 install ray[tune]'

    # The command to run.  Will be run under the working directory.
    def run_fn(ip_list: List[IPAddr]) -> Dict[IPAddr, str]:
        return {
            ip_list[0]: f'python3 tune_basic_example.py --server-address=auto',
        }

    train = sky.Task(
        'train',
        workdir=workdir,
        setup=setup,
        num_nodes=num_nodes,
        run=run_fn,
    )

    train.set_resources({
        sky.Resources(clouds.AWS(), 'p3.2xlarge'),
    })

dag = sky.Optimizer.optimize(dag, minimize=sky.Optimizer.COST)
sky.execute(dag)
