Job Queue
=========

We can schedule multiple jobs on a cluster using Sky's job queue. Using :code:`sky exec`, we can automatically queue each job for execution on the cluster. The :code:`-d` flag can be used to detach logging
from the terminal, which is useful for launching long-running jobs concurrently.

.. code-block:: bash

   # Launch the job 5 times
   sky exec mycluster task.yaml -d
   sky exec mycluster task.yaml -d
   sky exec mycluster task.yaml -d
   sky exec mycluster task.yaml -d
   sky exec mycluster task.yaml -d

If we wish to view the output for each run after it has completed we can use:

.. code-block:: bash

   # View the jobs in the queue
   sky queue mycluster

   # Pick a JOB_ID to view
   sky logs mycluster JOB_ID

To cancel a job, we can follow a similar pattern:

.. code-block:: bash

   # View the jobs in the queue
   sky queue mycluster

   # Pick a JOB_ID to view
   sky cancel mycluster JOB_ID

   # Cancel all jobs
   sky cancel mycluster --all
