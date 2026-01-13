.. _jobs-index:

Jobs
====

SkyPilot provides powerful job management capabilities for running AI workloads at scale.

.. contents:: Contents
   :local:
   :backlinks: none

Quick Links
----------

* :ref:`Recover by Exit Code <failure-recovery>` - Configure jobs to automatically recover on specific exit codes (e.g., NCCL timeouts, GPU driver issues)
* :ref:`Checkpointing and Recovery <checkpointing>` - Learn how to recover jobs from preemptions and failures using checkpointing
* :ref:`Managed Jobs <managed-jobs>` - Complete guide to managed jobs
* :ref:`Multi-Node Jobs <dist-jobs>` - Running distributed jobs
* :ref:`Many Parallel Jobs <many-jobs>` - Scaling to hundreds or thousands of jobs

Recover by Exit Code
--------------------

SkyPilot allows you to configure jobs to automatically recover when they exit with specific exit codes. This is particularly useful for transient errors like NCCL timeouts or GPU driver issues.

To configure recovery on specific exit codes, use the :code:`recover_on_exit_codes` field in your job's recovery configuration:

.. code-block:: yaml

  resources:
    accelerators: A100:8
    job_recovery:
      max_restarts_on_errors: 3
      # Always recover if the job exits with code 33 or 34
      recover_on_exit_codes: [33, 34]

For complete details, see the :ref:`Recovering on specific exit codes <failure-recovery>` section in the Managed Jobs guide.

Checkpointing and Recovery
--------------------------

One of the most important features for production jobs is the ability to recover from failures and preemptions. SkyPilot provides comprehensive checkpointing and recovery capabilities:

* :ref:`Checkpointing and recovery <checkpointing>` - Set up checkpointing to recover from spot instance preemptions
* :ref:`Jobs restarts on user code failure <failure-recovery>` - Configure automatic restarts on failures

For detailed information, see the :ref:`Checkpointing and recovery <checkpointing>` section in the Managed Jobs guide.

Managed Jobs
------------

:ref:`Managed Jobs <managed-jobs>` are the recommended way to run production workloads. They provide:

* Automatic recovery from spot instance preemptions
* Automatic retry on failures
* Automatic cleanup when done
* Easy management of many jobs in parallel

See the :ref:`Managed Jobs <managed-jobs>` guide for complete documentation.

Multi-Node Jobs
---------------

:ref:`Multi-Node Jobs <dist-jobs>` allow you to run distributed workloads across multiple nodes, with support for:

* Multi-node training (PyTorch DDP, DeepSpeed, etc.)
* Multi-node inference
* Custom distributed setups

See :ref:`Multi-Node Jobs <dist-jobs>` for details.

Many Parallel Jobs
------------------

:ref:`Many Parallel Jobs <many-jobs>` shows you how to:

* Run hundreds or thousands of jobs in parallel
* Manage hyperparameter sweeps
* Process large datasets with batch jobs

See :ref:`Many Parallel Jobs <many-jobs>` for examples and best practices.

Additional Resources
--------------------

* :ref:`Environment Variables <env-vars>` - Environment variables available in jobs
* :ref:`External Links <external-links>` - Add external links to jobs in the dashboard
* :ref:`Model Training Guide <training-guide>` - Best practices for training models

.. toctree::
   :maxdepth: 1
   :caption: Jobs Documentation
   :hidden:

   ../examples/managed-jobs
   distributed-jobs
   many-jobs
   environment-variables
   external-links
