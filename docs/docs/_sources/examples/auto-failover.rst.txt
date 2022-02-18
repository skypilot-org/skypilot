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
