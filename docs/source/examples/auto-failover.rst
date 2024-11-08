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

A common high-end GPU to use in AI is a NVIDIA A100 GPU.  These GPUs
are often in high demand and hard to get.  Let's see how SkyPilot's auto-failover
provisioner handles such a request:

.. code-block:: console

  $ sky launch -c gpu --gpus A100

  ...
  Launching a new cluster 'gpu'. Proceed? [Y/n]: 
  ⚙️ Launching on GCP us-central1 (us-central1-a).
  W 10-11 18:25:57 instance_utils.py:112] Got return codes 'VM_MIN_COUNT_NOT_REACHED', 'ZONE_RESOURCE_POOL_EXHAUSTED_WITH_DETAILS' in us-central1-a: 'Requested minimum count of 1 VMs could not be created'; "The zone 'projects/xxxxxx/zones/us-central1-a' does not have enough resources available to fulfill the request.  '(resource type:compute)'"
  ...

  ⚙️ Launching on GCP us-central1 (us-central1-f)
  ...

  ⚙️ Launching on GCP us-west1 (us-west1-a)
  ...
  ✓ Cluster launched: a100-8.  View logs at: ~/sky_logs/sky-2024-10-11-18-32-48-894132/provision.log

GCP was chosen as the best cloud to run the task. There was no capacity in any of the regions in US Central, so the auto-failover provisioner moved to US West instead, allowing for our instance to be successfully provisioned.

Cross-cloud failover
---------------------
If all regions within the chosen cloud failed, the provisioner retries on the next
cheapest cloud.

Here is an example of cross-cloud failover when requesting 8x A100 GPUs.  All
regions in Azure failed to provide the resource, so the provisioner switched to
GCP, where it succeeded after one region:

.. code-block:: console

  $ sky launch -c a100-8 --gpus A100:8

  Considered resources (1 node):
  ----------------------------------------------------------------------------------------------------
   CLOUD   INSTANCE              vCPUs   Mem(GB)   ACCELERATORS   REGION/ZONE     COST ($)   CHOSEN   
  ----------------------------------------------------------------------------------------------------
   Azure   Standard_ND96asr_v4   96      900       A100:8         eastus          27.20         ✔     
   GCP     a2-highgpu-8g         96      680       A100:8         us-central1-a   29.39               
   AWS     p4d.24xlarge          96      1152      A100:8         us-east-1       32.77               
  ----------------------------------------------------------------------------------------------------
  Launching a new cluster 'a100-8'. Proceed? [Y/n]: 

  ...
  ⚙️ Launching on Azure eastus.
  E 10-11 18:24:59 instance.py:457] Failed to create instances: [azure.core.exceptions.HttpResponseError] (InvalidTemplateDeployment)
  sky.exceptions.ResourcesUnavailableError: Failed to acquire resources in all zones in eastus
  ...

  ⚙️ Launching on GCP us-central1 (us-central1-a).
  W 10-11 18:25:57 instance_utils.py:112] Got return codes 'VM_MIN_COUNT_NOT_REACHED', 'ZONE_RESOURCE_POOL_EXHAUSTED_WITH_DETAILS' in us-central1-a: 'Requested minimum count of 1 VMs could not be created'; "The zone 'projects/xxxxxx/zones/us-central1-a' does not have enough resources available to fulfill the request.  '(resource type:compute)'"
  ...

  ⚙️ Launching on GCP us-central1 (us-central1-b).
    Instance is up.
  ✓ Cluster launched: a100-8.  View logs at: ~/sky_logs/sky-2024-10-11-18-24-14-357884/provision.log


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
  -----------------------------------------------------------------------------------------------------
   CLOUD   INSTANCE                 vCPUs   Mem(GB)   ACCELERATORS   REGION/ZONE   COST ($)   CHOSEN
  -----------------------------------------------------------------------------------------------------
   Azure   Standard_NV6ads_A10_v5   6       55        A10:1          eastus        0.45          ✔
   GCP     g2-standard-4            4       16        L4:1           us-east4-a    0.70
   AWS     g5.xlarge                4       16        A10G:1         us-east-1     1.01
  -----------------------------------------------------------------------------------------------------



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

This will generate the following output:

.. code-block:: console

  $ sky launch -c mycluster task.yaml
  ...

  Considered resources (1 node):
  ---------------------------------------------------------------------------------------------
   CLOUD   INSTANCE         vCPUs   Mem(GB)   ACCELERATORS   REGION/ZONE   COST ($)   CHOSEN
  ---------------------------------------------------------------------------------------------
   GCP     g2-standard-96   96      384       L4:8           us-east4-a    7.98          ✔
   AWS     g5.48xlarge      192     768       A10G:8         us-east-1     16.29
   GCP     a2-highgpu-8g    96      680       A100:8         us-east1-b    29.39
   AWS     p4d.24xlarge     96      1152      A100:8         us-east-1     32.77
  ---------------------------------------------------------------------------------------------

  Launching a new cluster 'mycluster'. Proceed? [Y/n]:
