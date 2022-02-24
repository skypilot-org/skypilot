Grid Search
===========

Searching over many hyperparameters often involves launching many jobs. In this example, we show
a simple way to perform grid search using Sky's job queue.

For this example, let's assume we've already provisioned a cluster with 4 V100 GPUs.

Submitting Trials
-------------------
Submitting multiple trials with different hyperparameters is simple:

.. code-block:: bash

  $ # Launch 4 trials in parallel
  $ sky exec mycluster --gpus V100:1 -d -- python train.py --lr 1e-3
  $ sky exec mycluster --gpus V100:1 -d -- python train.py --lr 3e-3
  $ sky exec mycluster --gpus V100:1 -d -- python train.py --lr 1e-4
  $ sky exec mycluster --gpus V100:1 -d -- python train.py --lr 1e-2

  # gets queued and will run once a GPU is available
  sky exec mycluster --gpus V100:1 -d -- python train.py --lr 1e-6

We use the :code:`-d` option to detach the run from the terminal, allowing us to
launch multiple runs concurrently. We can also specify resources for each job using
the :code:`--gpus` option. Note that this can be any number, and fractional counts are also
supported by the scheduling framework. Since there are only 4 V100 GPUs in total on the cluster,
Sky will queue 1 job while the other 4 run in parallel. Once a job finishes, the next job will
begin executing immediately.
