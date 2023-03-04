import os

import sky

workdir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'ray_tune_examples')

setup = 'pip3 install --upgrade pip && \
    pip3 install ray[tune] pytorch-lightning==1.5.10 lightning-bolts torchvision && \
    pip uninstall nvidia_cublas_cu11 -y'

# Using heterogeneous clusters, we can define different workdirs, resources,
# setup, and run commands for each task.
head_run = 'python3 tune_ptl_example_heterogeneous.py'
head_task = sky.Task('head',
                     workdir=workdir,
                     setup=setup,
                     run=head_run,
                     num_nodes=1)
head_task.set_resources({sky.Resources(sky.GCP(), 'n1-standard-4', 'V100')})

worker_task = sky.Task('worker', setup=setup, num_nodes=1)
worker_task.set_resources({sky.Resources(sky.GCP(), 'n1-standard-8', 'V100')})

sky.launch(sky.TaskGroup([head_task, worker_task]))
