.. _sky-api-server-performance-best-practices:

SkyPilot API Server Performance Best Practices
==============================================

This page describes performance best practices for remote SkyPilot API server in :ref:`team deployment <sky-api-server>`.

Tuning API server resources
---------------------------

The number of requests that the API server can handle concurrently is proportional to the resources (CPU cores and memory) allocated to it. Internally, requests are categorized into two different types and handled in a first-in-first-out manner:

* ``Long-running request``: request that takes long time and more resources to run, including ``launch``, ``exec``, ``jobs.launch``, etc.
* ``Short-running request``: request that takes short time or less resources to run, including ``status``, ``logs``, etc.

.. note::

    Though a task (or job) can run for any length of time, concurrent tasks does not occupy the concurrency. Because once a task is submitted to the cluster, it will be detached and no longer takes any resources off the API server.

It is recommended to tune the resources allocated to the API server based on the expected concurrency to avoid queuing:

.. tab-set::

    .. tab-item:: Helm Deployment

        .. code-block:: bash

            # NAMESPACE and RELEASE_NAME should be the same as the deployment step
            helm upgrade -n ${NAMESPACE} ${RELEASE_NAME} skypilot/skypilot-nightly \
                --reuse-values \
                --set apiService.resources.requests.cpu=8 \
                --set apiService.resources.requests.memory=16Gi \
                --set apiService.resources.limits.cpu=8 \
                --set apiService.resources.limits.memory=16Gi
            
        .. note:: 

            If you specify a resources that is lower than the minimum recommended resources (4 CPUs with 8GB of memory) for team usage, an error will be raised on ``helm upgrade``. You can specify ``--set apiService.skipResourcesCheck=true`` to skip the check if performance and stability is not an issue for you scenario.

        .. dropdown:: Why set the same value for the limits and requests?

            We recommend setting the resources limits to the same value as the requests for the following reasons:
            
            * API server depends on the resources limit to detect the available resources and decide the maximum concurrency. Setting limits larger than the requests or omitting the limits will cause the API server make aggressive concurrency decisions and may cause high resource contention on the Kubernetes node.
            * Setting the same value for the limits and requests ensures the QoS class of the API server pod being set to ``Guaranteed`` and reduce the chance of the pod being killed by the Kubernetes node when the node is under resource pressure.

            In short, setting the same value for the limits and requests sacrifices the resources utilization for stability and predictability. Pivoting to other trade-off is also possible, but we recommend to keep the memory request and limit the same in production environment to avoid potential eviction caused by node memory pressure.

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
   :widths: 1 1 2 2 1
   :header-rows: 1

   * - CPU
     - Memory
     - Long requests
     - Short requests
     - Suggested team size
   * - 4
     - 8Gi
     - 8 Max concurrency
     - 11 Max concurrency
     - 1~5 users
   * - 16
     - 32Gi
     - 32 Max concurrency
     - 60 Max concurrency
     - 1~15 users
   * - 32
     - 64Gi
     - 64 Max concurrency
     - 145 Max concurrency
     - 1~30 users
   * - 64
     - 128Gi
     - 128 Max concurrency
     - 299 Max concurrency
     - 10~50 users
   * - 128
     - 256Gi
     - 256 Max concurrency
     - 589 Max concurrency
     - 10~100 users

Queuing requests and polling status asynchronously
--------------------------------------------------

There is no limit on the number of queued requests, i.e. despite increasing the allocated resources to improve the maximum concurrency, you can also submit requests with :ref:`async<async>` (``--async``) and poll the status asynchronously to avoid blocking. For example:

.. code-block:: bash

    # Submit 2000 jobs at once without blocking
    for i in `seq 1 2000`; do
        sky jobs launch -y --async job.yaml
    done
    # Poll the status of the jobs
    watch -n 5 "sky jobs queue"

The requests will be queued on the API server and be processed in submission order. If you find the status is not updated for a while, you can inspect the status of the submitted requests with:

.. code-block:: console

    $ sky api status
    ID                                    User  Name             Created         Status
    d9c95c7e-d248-4a7f-b72e-636511405357  alice sky.jobs.launch  a few secs ago  PENDING
    767182fd-0202-4ae5-b2d7-ddfabea5c821  alice sky.jobs.launch  a few secs ago  PENDING
    5667cff2-e953-4b80-9e5f-546cea83dc59  alice sky.jobs.launch  a few secs ago  RUNNING

Check logs for a request
^^^^^^^^^^^^^^^^^^^^^^^^

There should be some ``RUNNING`` requests that occupy the concurrency limit. Usually the ``RUNNING`` requests make progress and finally your requests will be processed, but if the ``RUNNING`` requests are stuck, you can inspect the request log with:

.. code-block:: console

    # Replace <request_id> with the actual request id from the ID column
    $ sky api logs <request_id>

Cancel a request
^^^^^^^^^^^^^^^^

If the request is stuck according to the log, e.g. retrying to launch VMs that is out of stock, you can cancel the request with:

.. code-block:: bash

    sky api cancel <requst_id>

Avoid concurrent logs requests
------------------------------

If you run ``sky logs`` to tail the logs of a task, the log tailing will keep taking off the resources of the API server as long as the task being tailed is running. So concurrent log requests will occupy the concurrency and make other requests to be delayed.

To avoid this, it is recommended to run ``sky logs`` and ``sky jobs logs`` with ``--no-follow`` flag if there is a large number of concurrent log requests:

.. code-block:: bash

    sky logs --no-follow my_cluster

Commands that execute jobs like ``sky jobs launch`` and ``sky exec`` will also tail the logs of the job after the job is started by default. You can add ``--async`` flag to submit the job without tailing the logs:

.. code-block:: bash

    sky jobs launch --async job.yaml
