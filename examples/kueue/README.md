# Using Kueue with SkyPilot's Job Controller

SkyPilot can be used with [Kueue](https://kueue.sigs.k8s.io/) to manage queues and priorities in multi-tenant Kubernetes clusters.

## Prerequisites
Make sure you're on SkyPilot `job-api-k8s-labels` branch.

## 🛠️ Setting up Kueue

1. Setup [Kueue](https://kueue.sigs.k8s.io/docs/installation/) and [enable integration for v1/pod](https://kueue.sigs.k8s.io/docs/tasks/run/plain_pods/#before-you-begin).

```console
kubectl apply --server-side -f manifests.yaml
```

The following is an example enabling pod integration in `controller_manager_config.yaml` in the Kueue installation manifest:

```yaml
apiVersion: v1
data:
  controller_manager_config.yaml: |
    apiVersion: config.kueue.x-k8s.io/v1beta1
    kind: Configuration
    health:
      healthProbeBindAddress: :8081
    metrics:
      bindAddress: :8080
    webhook:
      port: 9443
    leaderElection:
      leaderElect: true
      resourceName: c1f6bfd2.kueue.x-k8s.io
    controller:
      groupKindConcurrency:
        Job.batch: 5
        Pod: 5
        Workload.kueue.x-k8s.io: 5
        LocalQueue.kueue.x-k8s.io: 1
        ClusterQueue.kueue.x-k8s.io: 1
        ResourceFlavor.kueue.x-k8s.io: 1
    clientConnection:
      qps: 50
      burst: 100
    integrations:
      frameworks:
      - "batch/job"
      - "pod"
      podOptions:
       # You can change namespaceSelector to define in which 
       # namespaces kueue will manage the pods.
       namespaceSelector:
         matchExpressions:
           - key: kubernetes.io/metadata.name
             operator: NotIn
             values: [ kube-system, kueue-system ]
```
2. Setup the `ClusterQueue` and `LocalQueue` for Kueue. Make sure `nvidia.com/gpu` is available in the resource flavor and under `coveredResources` in the `ClusterQueue` CR.
```console
kubectl apply -f single-clusterqueue-setup.yaml
```

## 🚀 Using Kueue with SkyPilot

0. Update your SkyPilot config YAML to set `provision_timeout: -1`, which will let jobs queue indefinitely. Also if your cluster uses exec based auth (e.g., GKE), add `remote_identity: SERVICE_ACCOUNT`. For example, in `~/.sky/config.yaml`:
    ```yaml
    kubernetes:
      provision_timeout: -1  # Wait indefinitely in the queue to get scheduled on the k8s cluster
      remote_identity: SERVICE_ACCOUNT # Set this field If you're using exec based auth, e.g., for GKE
    ```

1. Specify the queue name in the task YAML with the `resources.labels` field:
    ```yaml
    resources:
      cpus: 6
      cloud: kubernetes
      labels:
        kueue.x-k8s.io/queue-name: user-queue
    ...
    ```

2. 🎉SkyPilot is ready to run on Kueue! Launch your SkyPilot clusters as usual. The pods will be scheduled by Kueue based on the queue name specified in the pod metadata.

💡**Hint** - `sky job launch` will automatically launch the SkyPilot job controller on your K8s cluster.

For example, if you configured a 9 CPU `nominalQuota` for your `ClusterQueue`, running the `sky-kueue-task.yaml` as two tasks will launch the first job 
and then the second job will be queued until the first job is done:
```console
sky job launch -n job1 sky-kueue-task.yaml
sky job launch -n job2 sky-kueue-task.yaml
```

You can observe Kueue workload status by running the following command:
```console
$ kubectl get workloads -o wide
NAME                       QUEUE        ADMITTED BY     AGE
pod-job1-2ea4-head-52a69   user-queue   cluster-queue   67s
pod-job2-2ea4-head-8afd4   user-queue                   54s
```

## 🥾 Using priorities + preemption with Kueue [Optional]

Optionally, you can assign priorities to your SkyPilot jobs, and Kueue will preempt lower-priority jobs to run higher-priority jobs.

SkyPilot job controller will detect any preemptions, and re-submit the jobs which get preempted.

1. Before you start, make sure your `ClusterQueue` allows preemption through priorities by setting `withinClusterQueue: LowerPriority`. Refer to `single-clusterqueue-setup.yaml` for an example:
```yaml
...
  preemption:
    withinClusterQueue: LowerPriority
```

2. Create the desired `WorkloadPriorityClass` CRs. We have provided an example in `priority-classes.yaml`, which will create two priorities - `high-priority` and `low-priority`:
```console
kubectl apply -f priority-classes.yaml
```

3. Use the `kueue.x-k8s.io/priority-class` label to set the priority of the pod:
```yaml
resources:
  ...
  labels:
    ...
    kueue.x-k8s.io/priority-class: low-priority
```

4. 🎉SkyPilot jobs will now run with priorities on Kueue!

To demonstrate an example, first we will run a job with `low-priority` that uses 8 CPUs, assuming a 9 CPU quota. To start, edit your task (`sky-kueue-task.yaml`) to use `low-priority`:
```yaml
resources:
  ...
  labels:
    ...
    kueue.x-k8s.io/priority-class: low-priority
```

Then run launch the job with `sky launch` that uses 6 CPUs out of 9 CPU quota:
```console
sky job launch -n job1 sky-kueue-task.yaml
```

Now edit your task `sky-kueue-task.yaml` to use `high-priority` for its jobs:
```yaml
resources:
  ...
  labels:
    ...
    kueue.x-k8s.io/priority-class: high-priority
```

In a new terminal, launch the new job with that also uses 6 CPUs out of 9 CPU quota:
```console
sky job launch -n job2 sky-kueue-task.yaml
```

Now, if you run `sky job status`, you will see that the `low-priority` job is preempted by the `high-priority` job.

<!-- source https://docs.google.com/drawings/d/18tb04cJEIyF5afKxAPL2XKlHXIc2lLYR-PXOa1ImDnw/edit?usp=sharing -->
<p align="center">
  <img src="https://i.imgur.com/xbEcAWH.png" alt="Preemption example" width="600"/>
</p>

## ⚠️ Notes
* Preempted jobs are re-submitted by the SkyPilot controller, so the job will enter the queue at the back of the line. This may cause starvation for the preempted job in some cases. This can be addressed by having the job controller incrementally increase the priority of the preempted job.
* You can also specify labels to be applied globally in `~/.sky/config.yaml` by adding these labels under `kubernetes`. Task labels will override these global labels, if specified.
    ```yaml
    kubernetes:
      pod_config:
        metadata:
          labels:
            kueue.x-k8s.io/queue-name: user-queue
    ```
* The job controller is single-tenant - each user wanting to submit jobs will have their own job controller running. Eventually we can look into running a single job controller for all users.
* By setting `provision_timeout: <seconds>`, you can set a timeout for how long a job will wait to be scheduled on the Kubernetes cluster before bursting to the cloud. 