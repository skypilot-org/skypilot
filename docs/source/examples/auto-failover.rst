.. _auto-failover:

Auto-provisioning GPUs
==========================

SkyPilot comes with an auto-failover provisioner, which
**automatically retries provisioning** a cluster in different regions (or
clouds) if the requested resources cannot be provisioned.
Such failures can happen for a variety of reasons:

- Out of capacity (in a region or a cloud)
- Out of quotas (in a region or a cloud)

Auto-failover is especially useful for provisioning **scarce resources (e.g., GPUs/TPUs, high-count CPUs, spot instances)**.  The user is freed from manually
searching for regions (or clouds) that can provide the requested resources.

.. tip::

  No action is required to use this feature.  Auto-failover is automatically
  enabled whenever a new cluster is to be provisioned, such as during :code:`sky
  launch`.

  If specific :code:`cloud`, ``region``, or ``zone`` are requested for a
  task, auto-failover retries only within the specified location.

Provisioning GPUs
----------------------

To provision GPUs or other accelerators, use the ``resources.accelerators``
field if you are using a :ref:`task YAML <yaml-spec>`:

.. code-block:: yaml

  resources:
    accelerators: A100
    # accelerators: A100:1
    # accelerators: A100:8
    # accelerators: A100-80GB:8

Equivalently, you can use the :ref:`CLI argument <sky-launch>` ``--gpus`` in ``sky launch`` to specify the accelerators:

.. code-block:: console

  sky launch --gpus A100
  sky launch --gpus A100:1
  sky launch --gpus A100:8
  sky launch --gpus A100-80GB:8

Use ``sky show-gpus`` to see the names of all supported accelerators.

Cross-region failover
---------------------

The provisioner first retries across all regions within a task's chosen cloud.

A common high-end GPU to use in deep learning is a NVIDIA V100 GPU.  These GPUs
are often in high demand and hard to get.  Let's see how SkyPilot's auto-failover
provisioner handles such a request:

.. code-block:: console

  $ sky launch -c gpu --gpus V100
  ...  # optimizer output
  I 02-11 21:17:43 cloud_vm_ray_backend.py:1034] Creating a new cluster: "gpu" [1x GCP(n1-highmem-8, {'V100': 1.0})].
  I 02-11 21:17:43 cloud_vm_ray_backend.py:1034] Tip: to reuse an existing cluster, specify --cluster-name (-c) in the CLI or use sky.launch(.., cluster_name=..) in the Python API. Run `sky status` to see existing clusters.
  I 02-11 21:17:43 cloud_vm_ray_backend.py:614] To view detailed progress: tail -n100 -f sky_logs/sky-2022-02-11-21-17-43-171661/provision.log
  I 02-11 21:17:43 cloud_vm_ray_backend.py:624]
  I 02-11 21:17:43 cloud_vm_ray_backend.py:624] Launching on GCP us-central1 (us-central1-a)
  W 02-11 21:17:56 cloud_vm_ray_backend.py:358] Got ZONE_RESOURCE_POOL_EXHAUSTED in us-central1-a (message: The zone 'projects/intercloud-320520/zones/us-central1-a' does not have enough resources available to fulfill the request.  Try a different zone, or try again later.)
  ...
  I 02-11 21:18:24 cloud_vm_ray_backend.py:624] Launching on GCP us-central1 (us-central1-f)
  W 02-11 21:18:38 cloud_vm_ray_backend.py:358] Got ZONE_RESOURCE_POOL_EXHAUSTED in us-central1-f (message: The zone 'projects/intercloud-320520/zones/us-central1-f' does not have enough resources available to fulfill the request.  Try a different zone, or try again later.)
  I 02-11 21:18:38 cloud_vm_ray_backend.py:624]
  I 02-11 21:18:38 cloud_vm_ray_backend.py:624] Launching on GCP us-west1 (us-west1-a)
  Successfully connected to 35.230.120.87.

GCP was chosen as the best cloud to run the task. There was no capacity in any of the regions in US Central, so the auto-failover provisioner moved to US West instead, allowing for our instance to be successfully provisioned.

Cross-cloud failover
---------------------
If all regions within the chosen cloud failed, the provisioner retries on the next
cheapest cloud.

Here is an example of cross-cloud failover when requesting 8x V100 GPUs.  All
regions in GCP failed to provide the resource, so the provisioner switched to
AWS, where it succeeded after two regions:

.. code-block:: console

  $ sky launch -c v100-8 --gpus V100:8
  ...  # optimizer output
  I 02-23 16:39:59 cloud_vm_ray_backend.py:1010] Creating a new cluster: "v100-8" [1x GCP(n1-highmem-8, {'V100': 8.0})].
  I 02-23 16:39:59 cloud_vm_ray_backend.py:1010] Tip: to reuse an existing cluster, specify --cluster-name (-c) in the CLI or use sky.launch(.., cluster_name=..) in the Python API. Run `sky status` to see existing clusters.
  I 02-23 16:39:59 cloud_vm_ray_backend.py:658] To view detailed progress: tail -n100 -f sky_logs/sky-2022-02-23-16-39-58-577551/provision.log
  I 02-23 16:39:59 cloud_vm_ray_backend.py:668]
  I 02-23 16:39:59 cloud_vm_ray_backend.py:668] Launching on GCP us-central1 (us-central1-a)
  W 02-23 16:40:17 cloud_vm_ray_backend.py:403] Got ZONE_RESOURCE_POOL_EXHAUSTED in us-central1-a (message: The zone 'projects/intercloud-320520/zones/us-central1-a' does not have enough resources available to fulfill the request.  Try a different zone, or try again later.)
  ...
  I 02-23 16:42:15 cloud_vm_ray_backend.py:668] Launching on AWS us-east-2 (us-east-2a,us-east-2b,us-east-2c)
  W 02-23 16:42:26 cloud_vm_ray_backend.py:477] Got error(s) in all zones of us-east-2:
  W 02-23 16:42:26 cloud_vm_ray_backend.py:479]   create_instances: Attempt failed with An error occurred (InsufficientInstanceCapacity) when calling the RunInstances operation (reached max retries: 0): We currently do not have sufficient p3.16xlarge capacity in the Availability Zone you requested (us-east-2a). Our system will be working on provisioning additional capacity. You can currently get p3.16xlarge capacity by not specifying an Availability Zone in your request or choosing us-east-2b., retrying.
  ...
  I 02-23 16:42:26 cloud_vm_ray_backend.py:668]
  I 02-23 16:42:26 cloud_vm_ray_backend.py:668] Launching on AWS us-west-2 (us-west-2a,us-west-2b,us-west-2c,us-west-2d)
  I 02-23 16:47:04 cloud_vm_ray_backend.py:740] Successfully provisioned or found existing VM. Setup completed.


Multiple Candidate GPUs
-------------------------

If a task can be run on different GPUs, the user can specify multiple candidate GPUs,
and SkyPilot will automatically find the cheapest available GPU.

To allow SkyPilot to choose any of the candidate GPUs, specify a set of candidate GPUs in the task yaml:

.. code-block:: yaml

  resources:
    accelerators: {A10:1, L4:1, A10g:1}

In the above example, SkyPilot will try to provision the any cheapest available GPU within the set of
A10, L4, and A10g GPUs, using :code:`sky launch task.yaml`.

.. code-block:: console

  $ sky launch task.yaml
  ...
  I 11-19 08:07:45 optimizer.py:910] -----------------------------------------------------------------------------------------------------
  I 11-19 08:07:45 optimizer.py:910]  CLOUD   INSTANCE                 vCPUs   Mem(GB)   ACCELERATORS   REGION/ZONE   COST ($)   CHOSEN
  I 11-19 08:07:45 optimizer.py:910] -----------------------------------------------------------------------------------------------------
  I 11-19 08:07:45 optimizer.py:910]  Azure   Standard_NV6ads_A10_v5   6       55        A10:1          eastus        0.45          ✔
  I 11-19 08:07:45 optimizer.py:910]  GCP     g2-standard-4            4       16        L4:1           us-east4-a    0.70
  I 11-19 08:07:45 optimizer.py:910]  AWS     g5.xlarge                4       16        A10G:1         us-east-1     1.01
  I 11-19 08:07:45 optimizer.py:910] -----------------------------------------------------------------------------------------------------



To specify a preference order, use a list of candidate GPUs in the task yaml:

.. code-block:: yaml

  resources:
    accelerators: [A10:1, A10g:1, L4:1]

In the above example, SkyPilot will first try to provision an A10 GPU, then an A10g GPU, and finally an L4 GPU.

.. _multiple-resources:

Multiple Candidate Resources
--------------------------------------------

If a task would like to specify multiple candidate resources (not only GPUs), the user can specify a list of candidate resources with a preference annotation:


.. code-block:: yaml

  resources:
    ordered: # Candidate resources in a preference order
      - cloud: gcp
        accelerators: A100-80GB
      - instance_type: g5.xlarge
      - cloud: azure
        region: eastus
        accelerators: A100



.. code-block:: yaml

    resources:
      any_of: # Candidate resources that can be chosen in any order
        - cloud: gcp
          accelerators: A100-80GB
        - instance_type: g5.xlarge
        - cloud: azure
          region: eastus
          accelerators: A100

.. tip::

  The list items are specified with a leading prefix :code:`-`, and each item is a dictionary that
  includes the field for a candidate resource. :code:`ordered` and :code:`any_of` indicate the preference for the candidate resources.

**Example**: only allowing a set of regions/clouds for launching with any of the following GPUs: A10g:8, A10:8, L4:8, and A100:8:

.. code-block:: yaml

  resources:
    accelerators: {A10g:8, A10:8, L4:8, A100:8}
    any_of:
      # AWS:
      - region: us-east-1
      - region: us-east-2
      - region: us-west-1
      - region: us-west-2
      # GCP
      - region: us-central1
      - region: us-east1
      - region: us-east4
      - region: us-west1
      - region: us-west2
      - region: us-west3
      - region: us-west4

.. hint::

  The regions specified that does not have the accelerator will be ignored automatically.

This will genereate the following output:

.. code-block:: console

  $ sky launch -c mycluster task.yaml
  ...
  I 12-20 23:55:56 optimizer.py:717]
  I 12-20 23:55:56 optimizer.py:840] Considered resources (1 node):
  I 12-20 23:55:56 optimizer.py:910] ---------------------------------------------------------------------------------------------
  I 12-20 23:55:56 optimizer.py:910]  CLOUD   INSTANCE         vCPUs   Mem(GB)   ACCELERATORS   REGION/ZONE   COST ($)   CHOSEN
  I 12-20 23:55:56 optimizer.py:910] ---------------------------------------------------------------------------------------------
  I 12-20 23:55:56 optimizer.py:910]  GCP     g2-standard-96   96      384       L4:8           us-east4-a    7.98          ✔
  I 12-20 23:55:56 optimizer.py:910]  AWS     g5.48xlarge      192     768       A10G:8         us-east-1     16.29
  I 12-20 23:55:56 optimizer.py:910]  GCP     a2-highgpu-8g    96      680       A100:8         us-east1-b    29.39
  I 12-20 23:55:56 optimizer.py:910]  AWS     p4d.24xlarge     96      1152      A100:8         us-east-1     32.77
  I 12-20 23:55:56 optimizer.py:910] ---------------------------------------------------------------------------------------------
  I 12-20 23:55:56 optimizer.py:910]
  Launching a new cluster 'mycluster'. Proceed? [Y/n]:
