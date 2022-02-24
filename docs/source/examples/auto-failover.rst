.. _auto-failover:
Auto-provisioning GPUs
==========================

Provisioning an instance on a cloud can be difficult due to a variety of
reasons: lack of quota, insufficient capacity in the clouds, or certain
regions/clouds not providing the required resources.  These issues are
especially true for scarce resources such as GPUs and other accelerators, which
are required by many machine learning projects.

Sky solves this issue with an **auto-failover provisioner**, which automatically
retries provisioning for the user on a different region (or cloud) if the
requested resources cannot be provisioned.

A common high-end GPU to use in deep learning is a NVIDIA V100 GPU.  These GPUs
are often in high demand and hard to get.  Let's see how Sky's auto-failover
provisioner handles such a request:

.. code-block::

  $ sky gpunode -c gpu --gpus V100
  I 02-11 21:17:43 optimizer.py:211] Defaulting estimated time to 1 hr. Call Task.set_time_estimator() to override.
  I 02-11 21:17:43 optimizer.py:317] Optimizer - plan minimizing cost (~$3.0):
  I 02-11 21:17:43 optimizer.py:332]
  I 02-11 21:17:43 optimizer.py:332] TASK     BEST_RESOURCE
  I 02-11 21:17:43 optimizer.py:332] gpunode  GCP(n1-highmem-8, {'V100': 1.0})
  I 02-11 21:17:43 optimizer.py:332]
  I 02-11 21:17:43 optimizer.py:285] Considered resources -> cost
  I 02-11 21:17:43 optimizer.py:286] {AWS(p3.2xlarge): 3.06, GCP(n1-highmem-8, {'V100': 1.0}): 2.953212}
  I 02-11 21:17:43 optimizer.py:286]
  I 02-11 21:17:43 cloud_vm_ray_backend.py:1034] Creating a new cluster: "gpu" [1x GCP(n1-highmem-8, {'V100': 1.0})].
  I 02-11 21:17:43 cloud_vm_ray_backend.py:1034] Tip: to reuse an existing cluster, specify --cluster-name (-c) in the CLI or use sky.launch(.., cluster_name=..) in the Python API. Run `sky status` to see existing clusters.
  I 02-11 21:17:43 cloud_vm_ray_backend.py:614] To view detailed progress: tail -n100 -f sky_logs/sky-2022-02-11-21-17-43-171661/provision.log
  I 02-11 21:17:43 cloud_vm_ray_backend.py:624]
  I 02-11 21:17:43 cloud_vm_ray_backend.py:624] Launching on GCP us-central1 (us-central1-a)
  W 02-11 21:17:56 cloud_vm_ray_backend.py:358] Got ZONE_RESOURCE_POOL_EXHAUSTED in us-central1-a (message: The zone 'projects/intercloud-320520/zones/us-central1-a' does not have enough resources available to fulfill the request.  Try a different zone, or try again later.)
  I 02-11 21:17:56 cloud_vm_ray_backend.py:624]
  I 02-11 21:17:56 cloud_vm_ray_backend.py:624] Launching on GCP us-central1 (us-central1-b)
  W 02-11 21:18:10 cloud_vm_ray_backend.py:358] Got ZONE_RESOURCE_POOL_EXHAUSTED in us-central1-b (message: The zone 'projects/intercloud-320520/zones/us-central1-b' does not have enough resources available to fulfill the request.  Try a different zone, or try again later.)
  I 02-11 21:18:10 cloud_vm_ray_backend.py:624]
  I 02-11 21:18:10 cloud_vm_ray_backend.py:624] Launching on GCP us-central1 (us-central1-c)
  W 02-11 21:18:24 cloud_vm_ray_backend.py:358] Got ZONE_RESOURCE_POOL_EXHAUSTED in us-central1-c (message: The zone 'projects/intercloud-320520/zones/us-central1-c' does not have enough resources available to fulfill the request.  Try a different zone, or try again later.)
  I 02-11 21:18:24 cloud_vm_ray_backend.py:624]
  I 02-11 21:18:24 cloud_vm_ray_backend.py:624] Launching on GCP us-central1 (us-central1-f)
  W 02-11 21:18:38 cloud_vm_ray_backend.py:358] Got ZONE_RESOURCE_POOL_EXHAUSTED in us-central1-f (message: The zone 'projects/intercloud-320520/zones/us-central1-f' does not have enough resources available to fulfill the request.  Try a different zone, or try again later.)
  I 02-11 21:18:38 cloud_vm_ray_backend.py:624]
  I 02-11 21:18:38 cloud_vm_ray_backend.py:624] Launching on GCP us-west1 (us-west1-a)
  Successfully connected to 35.230.120.87.

There was no capacity in any of the regions in Central US, so the Sky auto-failover provisioner moved to West US instead, allowing for our instance to be successfully provisioned.

This feature is automatically enabled in all commands that provision new
resources, such as :code:`sky launch` or the interactive node commands
:code:`sky {gpunode,tpunode,cpunode}`.

Cross-cloud failover
----------------

The provisioner first retries in different regions of the chosen cloud, and then (if all those regions failed) retries on different clouds (typically, the next cheapest).

Here is an example of cross-cloud failover when requesting 8x V100 GPUs.  All regions in GCP failed to provide the resource, so the provisioner fell back to AWS, where it succeeded after two regions:

.. code-block::

  $ sky gpunode --gpus V100:8
  I 02-23 16:39:59 optimizer.py:213] Defaulting estimated time to 1 hr. Call Task.set_time_estimator() to override.
  I 02-23 16:39:59 optimizer.py:323] Optimizer - plan minimizing cost (~$20.3):
  I 02-23 16:39:59 optimizer.py:337]
  I 02-23 16:39:59 optimizer.py:337] TASK     BEST_RESOURCE
  I 02-23 16:39:59 optimizer.py:337] gpunode  GCP(n1-highmem-8, {'V100': 8.0})
  I 02-23 16:39:59 optimizer.py:337]
  I 02-23 16:39:59 optimizer.py:290] Considered resources -> cost
  I 02-23 16:39:59 optimizer.py:292] {GCP(n1-highmem-8, {'V100': 8.0}): 20.313212, AWS(p3.16xlarge): 24.48}
  I 02-23 16:39:59 optimizer.py:292]
  I 02-23 16:39:59 cloud_vm_ray_backend.py:1010] Creating a new cluster: "sky-gpunode-zongheng" [1x GCP(n1-highmem-8, {'V100': 8.0})].
  I 02-23 16:39:59 cloud_vm_ray_backend.py:1010] Tip: to reuse an existing cluster, specify --cluster-name (-c) in the CLI or use sky.launch(.., cluster_name=..) in the Python API. Run `sky status` to see existing clusters.
  I 02-23 16:39:59 cloud_vm_ray_backend.py:658] To view detailed progress: tail -n100 -f sky_logs/sky-2022-02-23-16-39-58-577551/provision.log
  I 02-23 16:39:59 cloud_vm_ray_backend.py:668]
  I 02-23 16:39:59 cloud_vm_ray_backend.py:668] Launching on GCP us-central1 (us-central1-a)
  W 02-23 16:40:17 cloud_vm_ray_backend.py:403] Got ZONE_RESOURCE_POOL_EXHAUSTED in us-central1-a (message: The zone 'projects/intercloud-320520/zones/us-central1-a' does not have enough resources available to fulfill the request.  Try a different zone, or try again later.)
  I 02-23 16:40:17 cloud_vm_ray_backend.py:668]
  I 02-23 16:40:17 cloud_vm_ray_backend.py:668] Launching on GCP us-central1 (us-central1-b)
  W 02-23 16:40:35 cloud_vm_ray_backend.py:403] Got ZONE_RESOURCE_POOL_EXHAUSTED in us-central1-b (message: The zone 'projects/intercloud-320520/zones/us-central1-b' does not have enough resources available to fulfill the request.  Try a different zone, or try again later.)
  I 02-23 16:40:35 cloud_vm_ray_backend.py:668]
  I 02-23 16:40:35 cloud_vm_ray_backend.py:668] Launching on GCP us-central1 (us-central1-c)
  W 02-23 16:40:55 cloud_vm_ray_backend.py:403] Got ZONE_RESOURCE_POOL_EXHAUSTED in us-central1-c (message: The zone 'projects/intercloud-320520/zones/us-central1-c' does not have enough resources available to fulfill the request.  Try a different zone, or try again later.)
  I 02-23 16:40:55 cloud_vm_ray_backend.py:668]
  I 02-23 16:40:55 cloud_vm_ray_backend.py:668] Launching on GCP us-central1 (us-central1-f)
  W 02-23 16:41:13 cloud_vm_ray_backend.py:403] Got QUOTA_EXCEEDED in us-central1-f (message: Quota 'NVIDIA_V100_GPUS' exceeded.  Limit: 1.0 in region us-central1.)
  I 02-23 16:41:13 cloud_vm_ray_backend.py:668]
  I 02-23 16:41:13 cloud_vm_ray_backend.py:668] Launching on GCP us-west1 (us-west1-a)
  W 02-23 16:41:31 cloud_vm_ray_backend.py:403] Got QUOTA_EXCEEDED in us-west1-a (message: Quota 'NVIDIA_V100_GPUS' exceeded.  Limit: 1.0 in region us-west1.)
  I 02-23 16:41:31 cloud_vm_ray_backend.py:668]
  I 02-23 16:41:31 cloud_vm_ray_backend.py:668] Launching on GCP us-east1 (us-east1-c)
  W 02-23 16:41:50 cloud_vm_ray_backend.py:403] Got ZONE_RESOURCE_POOL_EXHAUSTED in us-east1-c (message: The zone 'projects/intercloud-320520/zones/us-east1-c' does not have enough resources available to fulfill the request.  Try a different zone, or try again later.)
  E 02-23 16:41:50 cloud_vm_ray_backend.py:746] Failed to acquire resources in all regions/zones (requested GCP(n1-highmem-8, {'V100': 8.0})). Try changing resource requirements or use another cloud.
  W 02-23 16:41:50 cloud_vm_ray_backend.py:891]
  W 02-23 16:41:50 cloud_vm_ray_backend.py:891] Provision failed for GCP(n1-highmem-8, {'V100': 8.0}). Trying other launchable resources (if any)...
  I 02-23 16:41:50 optimizer.py:213] Defaulting estimated time to 1 hr. Call Task.set_time_estimator() to override.
  I 02-23 16:41:50 optimizer.py:323] Optimizer - plan minimizing cost (~$24.5):
  I 02-23 16:41:50 optimizer.py:337]
  I 02-23 16:41:50 optimizer.py:337] TASK     BEST_RESOURCE
  I 02-23 16:41:50 optimizer.py:337] gpunode  AWS(p3.16xlarge)
  I 02-23 16:41:50 optimizer.py:337]
  I 02-23 16:41:50 cloud_vm_ray_backend.py:658] To view detailed progress: tail -n100 -f sky_logs/sky-2022-02-23-16-39-58-577551/provision.log
  I 02-23 16:41:50 cloud_vm_ray_backend.py:668]
  I 02-23 16:41:50 cloud_vm_ray_backend.py:668] Launching on AWS us-east-1 (us-east-1a,us-east-1b,us-east-1c,us-east-1d,us-east-1e,us-east-1f)
  W 02-23 16:42:15 cloud_vm_ray_backend.py:477] Got error(s) in all zones of us-east-1:
  W 02-23 16:42:15 cloud_vm_ray_backend.py:479]   create_instances: Attempt failed with An error occurred (InsufficientInstanceCapacity) when calling the RunInstances operation (reached max retries: 0): We currently do not have sufficient p3.16xlarge capacity in the Availability Zone you requested (us-east-1a). Our system will be working on provisioning additional capacity. You can currently get p3.16xlarge capacity by not specifying an Availability Zone in your request or choosing us-east-1b, us-east-1d, us-east-1f., retrying.
  W 02-23 16:42:15 cloud_vm_ray_backend.py:479]   create_instances: Attempt failed with An error occurred (InsufficientInstanceCapacity) when calling the RunInstances operation (reached max retries: 0): We currently do not have sufficient p3.16xlarge capacity in the Availability Zone you requested (us-east-1b). Our system will be working on provisioning additional capacity. You can currently get p3.16xlarge capacity by not specifying an Availability Zone in your request or choosing us-east-1a, us-east-1d, us-east-1f., retrying.
  W 02-23 16:42:15 cloud_vm_ray_backend.py:479]   create_instances: Attempt failed with An error occurred (Unsupported) when calling the RunInstances operation: Your requested instance type (p3.16xlarge) is not supported in your requested Availability Zone (us-east-1c). Please retry your request by not specifying an Availability Zone or choosing us-east-1a, us-east-1b, us-east-1d, us-east-1f., retrying.
  W 02-23 16:42:15 cloud_vm_ray_backend.py:479]   create_instances: Attempt failed with An error occurred (InsufficientInstanceCapacity) when calling the RunInstances operation (reached max retries: 0): We currently do not have sufficient p3.16xlarge capacity in the Availability Zone you requested (us-east-1d). Our system will be working on provisioning additional capacity. You can currently get p3.16xlarge capacity by not specifying an Availability Zone in your request or choosing us-east-1a, us-east-1b, us-east-1f., retrying.
  W 02-23 16:42:15 cloud_vm_ray_backend.py:479]   create_instances: Attempt failed with An error occurred (Unsupported) when calling the RunInstances operation: Your requested instance type (p3.16xlarge) is not supported in your requested Availability Zone (us-east-1e). Please retry your request by not specifying an Availability Zone or choosing us-east-1a, us-east-1b, us-east-1d, us-east-1f., retrying.
  W 02-23 16:42:15 cloud_vm_ray_backend.py:479]   botocore.exceptions.ClientError: An error occurred (InsufficientInstanceCapacity) when calling the RunInstances operation (reached max retries: 0): We currently do not have sufficient p3.16xlarge capacity in the Availability Zone you requested (us-east-1f). Our system will be working on provisioning additional capacity. You can currently get p3.16xlarge capacity by not specifying an Availability Zone in your request or choosing us-east-1a, us-east-1b, us-east-1d.
  I 02-23 16:42:15 cloud_vm_ray_backend.py:668]
  I 02-23 16:42:15 cloud_vm_ray_backend.py:668] Launching on AWS us-east-2 (us-east-2a,us-east-2b,us-east-2c)
  W 02-23 16:42:26 cloud_vm_ray_backend.py:477] Got error(s) in all zones of us-east-2:
  W 02-23 16:42:26 cloud_vm_ray_backend.py:479]   create_instances: Attempt failed with An error occurred (InsufficientInstanceCapacity) when calling the RunInstances operation (reached max retries: 0): We currently do not have sufficient p3.16xlarge capacity in the Availability Zone you requested (us-east-2a). Our system will be working on provisioning additional capacity. You can currently get p3.16xlarge capacity by not specifying an Availability Zone in your request or choosing us-east-2b., retrying.
  W 02-23 16:42:26 cloud_vm_ray_backend.py:479]   create_instances: Attempt failed with An error occurred (InsufficientInstanceCapacity) when calling the RunInstances operation (reached max retries: 0): We currently do not have sufficient p3.16xlarge capacity in the Availability Zone you requested (us-east-2b). Our system will be working on provisioning additional capacity. You can currently get p3.16xlarge capacity by not specifying an Availability Zone in your request or choosing us-east-2a., retrying.
  W 02-23 16:42:26 cloud_vm_ray_backend.py:479]   create_instances: Attempt failed with An error occurred (Unsupported) when calling the RunInstances operation: Your requested instance type (p3.16xlarge) is not supported in your requested Availability Zone (us-east-2c). Please retry your request by not specifying an Availability Zone or choosing us-east-2a, us-east-2b., retrying.
  W 02-23 16:42:26 cloud_vm_ray_backend.py:479]   create_instances: Attempt failed with An error occurred (InsufficientInstanceCapacity) when calling the RunInstances operation (reached max retries: 0): We currently do not have sufficient p3.16xlarge capacity in the Availability Zone you requested (us-east-2a). Our system will be working on provisioning additional capacity. You can currently get p3.16xlarge capacity by not specifying an Availability Zone in your request or choosing us-east-2b., retrying.
  W 02-23 16:42:26 cloud_vm_ray_backend.py:479]   botocore.exceptions.ClientError: An error occurred (InsufficientInstanceCapacity) when calling the RunInstances operation (reached max retries: 0): We currently do not have sufficient p3.16xlarge capacity in the Availability Zone you requested (us-east-2b). Our system will be working on provisioning additional capacity. You can currently get p3.16xlarge capacity by not specifying an Availability Zone in your request or choosing us-east-2a.
  I 02-23 16:42:26 cloud_vm_ray_backend.py:668]
  I 02-23 16:42:26 cloud_vm_ray_backend.py:668] Launching on AWS us-west-2 (us-west-2a,us-west-2b,us-west-2c,us-west-2d)
  I 02-23 16:47:04 cloud_vm_ray_backend.py:740] Successfully provisioned or found existing VM. Setup completed.
