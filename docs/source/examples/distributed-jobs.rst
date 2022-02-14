Distributed Jobs on many VMs
================================================

For certain workloads, a single node may not be sufficient. Sky supports multi-node cluster
provisioning and distributed execution on many VMs. Currently, this functionality is only
supported using the Python API.

We define a simple distributed training example below:

.. code-block:: python

  import sky

  with sky.Dag() as dag:
    num_nodes = 2  # total nodes, INCLUDING head node

    setup = """
    #!/bin/bash
    pip3 install --upgrade pip
    git clone https://github.com/michaelzhiluo/pytorch-distributed-resnet
    cd pytorch-distributed-resnet
    pip3 install -r requirements.txt
    mkdir -p data
    mkdir -p saved_models
    mkdir -p data
    wget -c --quiet https://www.cs.toronto.edu/~kriz/cifar-10-python.tar.gz
    tar -xvzf cifar-10-python.tar.gz
    """

    # Define a distributed training job by providing a dictionary of
    # commands to run on each distributed node along with its IP address.
    def run_fn(ip_list):
      run_dict = {}  # dictionary of ip -> run command mapping
      num_workers = len(ip_list)
      master_ip = ip_list[0]
      for i, ip in enumerate(ip_list):
        run_dict[ip] = f"""
        cd pytorch-distributed-resnet
        python3 -m torch.distributed.launch resnet_ddp.py \
          --nproc_per_node=1 \
          --nnodes={num_workers} \
          --node_rank={i} \
          --master_addr=\"{master_ip}\" \
          --master_port=8008 \
          --num_epochs 20
        """

      return run_dict

    # Create a Sky task
    task = sky.Task(
      'distributed_training',
      setup=setup,
      num_nodes=num_nodes,
      run=run_fn,
    )

    # Set task resources
    # Each node on the cluster will use the resources specified below
    resources = sky.Resources(sky.AWS(), 'p3.2xlarge')
    train.set_resources(resources)


  # Launch the task!
  sky.launch(dag)