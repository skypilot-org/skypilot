
.. _reservation:

Reservations & DWS
===================


Clouds are designed to be elastic, but due to the recent GPU shortages, many organizations have started to get reservations
from cloud providers to lock in GPU capacity.

SkyPilot can be used to request resources from reservations and even combine them with on-demand/spot resources to fully
utilize the capacity in your cloud accounts.

.. image:: https://i.imgur.com/UY9eD1r.png
  :width: 90%
  :align: center


AWS Capacity Reservations & Capacity Blocks
--------------------------------------------

AWS **capacity reservations** and **capacity blocks** are ways to reserve a certain amount of compute capacity for a period of time. The latter is for high-end GPUs, such as A100s (P4d instances) and H100s (P5d instances), while the former is for all other instance types.
Instead of committing to a 1-3 year long contract, you can get a capacity reservation or capacity block for as short as 1 second or 1 day, respectively.


To request capacity reservations/blocks, see the official docs:

* `AWS Capacity Reservations <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-reservations.html>`_
* `AWS Capacity Blocks <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-blocks.html>`_

Once you have successfully created a reservation/block, you will get an ID of the reservation/block, such as ``cr-012345678``.

To use the reservation/block, you can specify two fields in ``~/.sky/config.yaml``:

* ``aws.prioritize_reservations``: whether to prioritize launching clusters from capacity reservations in any region/zone over on-demand/spot clusters. This is useful to fully utilize your reserved capacity created with ``Instance eligibility: open``.
* ``aws.specific_reservations``: a list of reservation IDs that can be used by SkyPilot. This is useful if you have multiple capacity reservations or blocks with ``Instance eligibility: targeted`` for different instance types in multiple regions/zones.

.. note::

    If any of the fields are specified, please allow about 30 seconds for SkyPilot optimizer to retrieve the latest reservation/block status on all regions and zones from your AWS account.

Example:

.. code-block:: yaml

    aws:
      prioritize_reservations: true
      specific_reservations:
        # 1x H100 capacity block in us-east-1
        - "cr-0123456789"
        # 2x A100 reservation in us-east-2
        - "cr-123456789a"
        # 2x A100 reservation in us-west-2
        - "cr-23456789ab"
        # 2x M5a.16xlarge reservation in us-east-1
        - "cr-3456789abc"

For more details of the fields, see :ref:`config-yaml`.



.. _utilizing-reservations:

Utilizing Reservations
~~~~~~~~~~~~~~~~~~~~~~

By specifying configurations above, SkyPilot will prioritize using any available resources in reservation/block (i.e., consider them as zero cost) whenever you launch a cluster/job.

For example, if you are launching a cluster with the following SkyPilot YAML:

.. code-block:: yaml

    resources:
      cloud: aws
      accelerators: A100:8
    
    num_nodes: 2


SkyPilot will utilize the capacity reservation as follows:

1. Find available capacity in your capacity blocks in ``us-east-2`` and ``us-west-2`` in the reservation ``cr-123456789a`` and ``cr-23456789ab``, respectively. Let's say 1 A100 instance capacity is available in ``us-east-2`` but no available capacity in ``us-west-2`` (as shown in the figure above).
2. SkyPilot will first try to launch 2 nodes in ``us-east-2`` with 1 node coming from the reservation, and the rest 1 node as on-demand.
3. If it fails to find available an on-demand A100 instance in ``us-east-2``, it will automatically :ref:`failover <auto-failover>` to other clouds/regions/zones.


.. hint::

    If you have a capacity block with a starting time in the future, you can run ``sky jobs launch --region us-east-1 --gpus H100:8 task.yaml`` and let SkyPilot to automatically wait until the starting time comes. Namely, you don't have to wake up at 4:30am PDT to launch your job on a newly available capacity block.


GCP reservations
-----------------

GCP reservations are similar to AWS capacity reservations, where you can reserve a certain amount of compute capacity for any period of time.

To get a reservation, see the `GCP official docs <https://cloud.google.com/compute/docs/instances/reservations-single-project>`__.

Like AWS, you can specify two fields in ``~/.sky/config.yaml``:

* ``gcp.prioritize_reservations``: whether to prioritize launching clusters from reservations in any region/zone over on-demand/spot clusters. This is useful to fully utilize your `automatically consumed reservations <https://cloud.google.com/compute/docs/instances/reservations-consume#consuming_instances_from_any_matching_reservation>`__.
* ``gcp.specific_reservations``: a list of reservation IDs that can be used by SkyPilot. This is useful if you have multiple `specific reservations <https://cloud.google.com/compute/docs/instances/reservations-consume#consuming_instances_from_a_specific_reservation>`__ for different instance types in multiple regions/zones.

Example:

.. code-block:: yaml

    gcp:
      prioritize_reservations: true
      specific_reservations:
        - projects/my-project/reservations/my-reservation1
        - projects/my-project/reservations/my-reservation2


SkyPilot will utilize the reservations similar to AWS reservations as described in :ref:`utilizing-reservations`.


GCP Dynamic Workload Scheduler (DWS)
-------------------------------------

GCP `Dynamic Workload Scheduler (DWS) <https://cloud.google.com/blog/products/compute/introducing-dynamic-workload-scheduler>`__ is a resource management service that allows you submit a GPU capacity request, automatically provisions the requested resources when they are available and keeps the resources running for a specified duration.

.. hint::

    It has been observed that using DWS can significantly increase the chance of getting a high-end GPU resource, such as A100s and H100s, compared to using on-demand or spot instances.


Using DWS for VMs
~~~~~~~~~~~~~~~~~

SkyPilot allows you to launch a cluster with DWS by specifying the ``gcp.managed_instance_group`` field in ``~/.sky/config.yaml``:

.. code-block:: yaml

    gcp:
      managed_instance_group:
        run_duration: 3600
        provision_timeout: 900


1. ``run_duration``: duration for a created instance to be kept alive (in seconds, required).
2. ``provision_timeout``: timeout for provisioning an instance with DWS (in seconds, optional). If the timeout is reached without requested resources being provisioned, SkyPilot will automatically :ref:`failover <auto-failover>` to other clouds/regions/zones to get the resources.

See the :ref:`config-yaml` for more details.

In case you want to specify the DWS configuration for each job/cluster, you can also specify the configuration in the SkyPilot task YAML (see :ref:`here <task-yaml-experimental>`):

.. code-block:: yaml

    experimental:
      config_overrides:
        gcp:
          managed_instance_group:
            run_duration: 3600
            provision_timeout: 900

    resources:
      cloud: gcp
      accelerators: A100:8
    
    num_nodes: 4
    
Using DWS on GKE with Kueue
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

DWS is also supported on Google Kubernetes Engine (GKE) with Kueue. To enable DWS on GKE, you need to set up your GKE cluster with Kueue and DWS, see the `GCP official docs <https://cloud.google.com/kubernetes-engine/docs/how-to/provisioningrequest>`

To launch a SkyPilot cluster or job on GKE with DWS, you can specify the DWS configuration in the SkyPilot task YAML:

.. code-block:: yaml

    experimental:
      config_overrides:
        kubernetes:
          pod_config:
            metadata:
              annotations:
                provreq.kueue.x-k8s.io/maxRunDurationSeconds: "3600"
          provision_timeout: 900

    resourcse:
      cloud: kubernetes
      accelerators: A100:8
      labels:
        kueue.x-k8s.io/queue-name: dws-local-queue

1. ``kueue.x-k8s.io/queue-name``: name of the Kueue queue to submit your resource request to.
2. ``provreq.kueue.x-k8s.io/maxRunDurationSeconds``: maximum duration for a created instance to be kept alive (in seconds, required).
3. ``provision_timeout``: timeout for provisioning an instance with DWS (in seconds, optional). If the timeout is reached without getting the requested resources, SkyPilot will automatically :ref:`failover <auto-failover>` to other clouds/regions/zones to get the resources.

Long-term reservations
----------------------

Unlike short-term reservations above, long-term reservations are typically more than one month long and can be viewed as a type of *on-prem cluster*.

SkyPilot supports long-term reservations and on-premise clusters through Kubernetes, i.e., you can set up a Kubernetes cluster on top of your reserved resources and interact with them through SkyPilot.

See the simple steps to set up a Kubernetes cluster on existing machines in :ref:`kubernetes-overview`.

