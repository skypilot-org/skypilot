.. _grid-search:
Grid Search
===========

To submit multiple trials with different hyperparameters to a cluster:

.. code-block:: bash

  $ sky exec mycluster --gpus V100:1 -d -- python train.py --lr 1e-3
  $ sky exec mycluster --gpus V100:1 -d -- python train.py --lr 3e-3
  $ sky exec mycluster --gpus V100:1 -d -- python train.py --lr 1e-4
  $ sky exec mycluster --gpus V100:1 -d -- python train.py --lr 1e-2
  $ sky exec mycluster --gpus V100:1 -d -- python train.py --lr 1e-6

Options used:

- :code:`--gpus`: specify the resource requirement for each job.
- :code:`-d` / :code:`--detach`: detach the run and logging from the terminal, allowing multiple trials to run concurrently.

If there are only 4 V100 GPUs on the cluster, Sky will queue 1 job while the
other 4 run in parallel. Once a job finishes, the next job will begin executing
immediately.
Refer to :ref:`Job Queue <job-queue>` for more details on Sky's scheduling behavior.


Multiple trials per GPU
-----------

To run multiple trials per GPU, use *fractional counts* in the resource requirement.
For example, use :code:`--gpus V100:0.5` to make 2 trials share 1 GPU:

.. code-block:: bash

  $ sky exec mycluster --gpus V100:0.5 -d -- python train.py --lr 1e-3
  $ sky exec mycluster --gpus V100:0.5 -d -- python train.py --lr 3e-3
  ...
