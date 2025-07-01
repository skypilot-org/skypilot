# Provision GPUs with Flex-Start Provisioning Mode in GKE with SkyPilot

Flex-start, powered by [Dynamic Workload Scheduler](https://cloud.google.com/blog/products/compute/introducing-dynamic-workload-scheduler), provides a flexible and cost-effective technique to obtain GPUs when you need to run AI/ML workloads.

GKE supports the two kinds of flex-start configurations:

* Flex-start, where GKE allocates resources node by node. This configuration only requires you to set the `--flex-start` flag during node creation.
* Flex-start with queued provisioning, where GKE allocates all requested resources at the same time. To use this configuration, you have to add the `--flex-start` and `enable-queued-provisioning` flags when you create the node pool. 

Refer to [flex-start documentation](https://cloud.google.com/kubernetes-engine/docs/concepts/dws) for more details.

## Flex-Start

Flex-start configuration is recommended for the small to medium workload size, which means that the workload can run on a single node. For example, this configuration works well if you are running small training jobs, offline inference, or batch jobs.

To launch clusters or managed jobs with flex-start configuration:

1. Follow the [GKE documentation](https://cloud.google.com/kubernetes-engine/docs/how-to/dws-flex-start-training#node-pool-flex) to create a node pool with flex-start enabled.

2. Configure the following fields in `~/.sky/config.yaml`:

```bash
kubernetes:
  # provision_timeout: 900
  autoscaler: gke
  dws:
    enable_flex_start: true
```

The default `provision_timeout` is set to `600` with flex-start enabled, if you still hit the provisioning timeout issue, please increase the `provision_timeout`.

3. Launch your clusters or managed jobs.

```bash
name: flex-start

resources:
  infra: k8s
  accelerators: L4:1

num_nodes: 1
```

## Flex-Start with Queued Provisioning

Flex-start with queued provisioning configuration is recommended for the medium to large workload, which means that the workload can run on multiple nodes. Your workload requires multiple resources and can't start running until all nodes are provisioned and ready at the same time. For example, this configuration works well if you are running distributed machine learning training workloads.

It is recommended to use [Kueue](https://kueue.sigs.k8s.io/) to run your batch and AI workloads with flex-start with queued provisioning.

To launch clusters or managed jobs with flex-start with queued provisioning configuration:

1. Follow the [GKE documentation](https://cloud.google.com/kubernetes-engine/docs/how-to/provisioningrequest#create-node-pool) to create a node pool with flex-start with queued provisioning enabled.

2. Follow the [documentation](https://docs.skypilot.co/en/latest/reference/kubernetes/examples/kueue-example.html) to install Kueue, update configuration of Kueue to support plain Pods, create Kueue resource flavor, cluster queue and local queue.

3. Configure the following fields in `~/.sky/config.yaml`:

```bash
kubernetes:
  # provision_timeout: 900
  autoscaler: gke
  dws:
    enable_flex_start_queued_provisioning: true
    # Optional, the maximum runtime of a node in seconds,
    # up to the default of seven days
    max_run_duration_seconds: 600
  kueue:
    local_queue_name: skypilot-local-queue
```

The default `provision_timeout` is set to `600` with flex-start with queued provisioning enabled, if you still hit the provisioning timeout issue, please increase the `provision_timeout`.

4. Launch your clusters or managed jobs.

```bash
name: flex-start-queued-provisioning

resources:
  infra: k8s
  accelerators: L4:1

num_nodes: 2
```
