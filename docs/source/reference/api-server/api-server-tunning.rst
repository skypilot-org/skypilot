.. _sky-api-server-performance-best-practices:

SkyPilot API Server Performance Best Practices
==============================================

This page describes performance best practices for centralized SkyPilot API server in team deployment.

Tuning API server resources
---------------------------

The concurrent requests that the API server can handle is proportional to the resources (CPU cores and memory) allocated to it. Requests are separated into two different queues and handled in a first-in-first-out manner:

* ``Long queue``: for long-running requests like ``launch``, ``exec`` and ``jobs launch``;
* ``Short queue``: for short-running requests like ``status`` and ``logs``;

It is recommended to tune the resources allocated to the API server based on the expected concurrency to avoid queuing:

.. tab-set::

    .. tab-item:: Helm Deployment

        .. code-block:: bash

            # NAMESPACE and RELEASE_NAME should be the same as the deployment step
            helm upgrade -n ${NAMESPACE} ${RELEASE_NAME} skypilot/skypilot-nightly \
                --reuse-values \
                --set apiService.resources.requests.cpu=8 \
                --set apiService.resources.requests.memory=16Gi
            
        .. note:: 

            If you specify a resources that is lower than the minimum recommended resources for team usage, an error will be raised on ``helm upgrade``. You can specify ``--set apiService.skipResourcesCheck=true`` to skip the check if performance and stability is not an issue for you scenario.

    .. tab-item:: VM Deployment

        For VM deployment, in-place vertically scaling the API server is not supported and the API server need to be terminated and recreated to apply the new resources. This means the current state of the API server will be lost. We recommend to create an new API server instance with the new resources and gradually migrate the workload to the new API server.

        Refer to :ref:`sky-api-server-cloud-deploy` for how to deploy the new API server and modify the cluster configuration before running ``sky launch``:

        .. code-block:: diff

            resources:
            -   cpus: 8+
            -   memory: 16+
            +   cpus: 16+
            +   memory: 32+

The following table shows the maximum concurrency for different resource configurations:

.. list-table::
   :widths: 1 1 2 2
   :header-rows: 1

   * - CPU
     - Memory
     - Long requests
     - Short requests
   * - 4
     - 8Gi
     - 8 Max concurrency
     - 11 Max concurrency
   * - 16
     - 32Gi
     - 32 Max concurrency
     - 60 Max concurrency
   * - 32
     - 64Gi
     - 64 Max concurrency
     - 145 Max concurrency
   * - 64
     - 128Gi
     - 128 Max concurrency
     - 299 Max concurrency
   * - 128
     - 256Gi
     - 256 Max concurrency
     - 589 Max concurrency

Queuing requests and polling status asynchronously
--------------------------------------------------

There is no limit on the number of queued requests. So in addition to increasing the allocated resources to improve the maximum concurrency, you can also submit requests with ``--async`` flag and poll the status asynchronously to avoid blocking. For example:

.. code-block:: bash

    # Submit 2000 jobs at once without blocking
    for i in `seq 1 2000`; do
        sky jobs launch --async job.yaml
    done
    # Poll the status of the jobs
    sky jobs queue

The requests will be queued on the server and be processed in submission order. If you find the status is not updated for a while, you can inspect the status of the submitted requests with:

.. code-block:: console

    $ sky api status
    ID                                    User             Name             Created         Status
    d9c95c7e-d248-4a7f-b72e-636511405357  alice            sky.jobs.launch  a few secs ago  PENDING
    767182fd-0202-4ae5-b2d7-ddfabea5c821  alice            sky.jobs.launch  a few secs ago  PENDING
    5667cff2-e953-4b80-9e5f-546cea83dc59  alice            sky.jobs.launch  a few secs ago  RUNNING

There should be some ``RUNNING`` requests that occupy the concurrency limit. Usually the ``RUNNING`` requests make progress and finally your requests will be processed, but if the ``RUNNING`` requests are stuck, you can inspect the request log with:

.. code-block:: console

    # Replace <request_id> with the actual request id from the ID column
    $ sky api logs <request_id>

If the request is stuck according to the log, e.g. retrying to launch VMs that is out of stock, you can cancel the request with:

.. code-block:: bash

    sky api cancel <requst_id>
