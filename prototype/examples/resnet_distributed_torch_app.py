from typing import Dict, List

import sky

IPAddr = str

with sky.Dag() as dag:
    # Total Nodes, INCLUDING Head Node
    num_nodes = 2

    # The setup command.  Will be run under the working directory.
    setup = 'echo \"alias python=python3\"" >> ~/.bashrc && pip3 install --upgrade pip && \
      git clone https://github.com/michaelzhiluo/pytorch-distributed-resnet && \
      cd pytorch-distributed-resnet && pip3 install -r requirements.txt && \
      mkdir -p data  && mkdir -p saved_models && cd data && \
      wget -c --quiet https://www.cs.toronto.edu/~kriz/cifar-10-python.tar.gz && \
      tar -xvzf cifar-10-python.tar.gz'

    # The command to run.  Will be run under the working directory.
    def run_fn(ip_list: List[IPAddr]) -> Dict[IPAddr, str]:
        run_dict = {}
        num_nodes = len(ip_list)
        master_ip = ip_list[0]
        for i, ip in enumerate(ip_list):
            run_dict[ip] = f'cd pytorch-distributed-resnet && \
            python3 -m torch.distributed.launch --nproc_per_node=1 \
            --nnodes={num_nodes} --node_rank={i} --master_addr=\"{master_ip}\" \
            --master_port=8008 resnet_ddp.py --num_epochs 20'

        return run_dict

    run = run_fn

    train = sky.Task(
        'train',
        setup=setup,
        num_nodes=num_nodes,
        run=run,
    )

    train.set_resources({
        ##### Fully specified
        sky.Resources(sky.AWS(), 'p3.2xlarge'),
        # sky.Resources(sky.GCP(), 'n1-standard-16'),
        #sky.Resources(
        #     sky.GCP(),
        #     'n1-standard-8',
        # Options: 'V100', {'V100': <num>}.
        #     'V100',
        #),
        ##### Partially specified
        #sky.Resources(accelerators='V100'),
        # sky.Resources(accelerators='tpu-v3-8'),
        # sky.Resources(sky.AWS(), accelerators={'V100': 4}),
        # sky.Resources(sky.AWS(), accelerators='V100'),
    })

dag = sky.Optimizer.optimize(dag, minimize=sky.Optimizer.COST)
sky.execute(dag)
