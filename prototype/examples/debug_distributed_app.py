import json
from typing import Dict, List

import sky
import time_estimators
from sky import clouds

IPAddr = str

with sky.Dag() as dag:
    # Total Nodes, INCLUDING Head Node
    num_nodes = 2

    # The setup command.  Will be run under the working directory.
    setup = 'pip3 install --upgrade pip && \
           pip3 install ray[default]'

    # The command to run.  Will be run under the working directory.
    def run_fn(ip_list: List[IPAddr]) -> Dict[IPAddr, str]:
        import random
        return {
            ip: f"ls -a ./ && mkdir -p {str(random.randint(0, 100000000))}"
            for ip in ip_list
        }

    run = run_fn

    train = sky.Task(
        'train',
        setup=setup,
        num_nodes=num_nodes,
        run=run,
    )

    train.set_resources({
        ##### Fully specified
        sky.Resources(clouds.AWS(), 'p3.2xlarge'),
    })

dag = sky.Optimizer.optimize(dag, minimize=sky.Optimizer.COST)
sky.execute(dag)
