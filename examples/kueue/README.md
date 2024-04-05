# Using Kueue with SkyPilot

SkyPilot can be used with [Kueue](https://kueue.sigs.k8s.io/) to manage queues and priorities in multi-tenant Kubernetes clusters. 

## Prerequisites
Make sure you're on commit `c2a30082586787d9cef83c21f1da3fdeb4c7b346` or later. Nightly builds on or after `skypilot-nightly==1.0.0.dev20240404` will also work.

## 🛠️ Setting up Kueue

1. Setup [Kueue](https://kueue.sigs.k8s.io/docs/installation/) and [enable integration for v1/pod](https://kueue.sigs.k8s.io/docs/tasks/run/plain_pods/#before-you-begin). The following is an example enabling pod integration in `controller_manager_config.yaml` in the Kueue installation manifest:

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

1. Update your SkyPilot config YAML to use the Kueue scheduler by using `kueue.x-k8s.io/queue-name` label in the pod metadata. Additionally, set `provision_timeout: 0` to let jobs queue indefinitely. For example, the following config will use the `user-queue` queue for scheduling the pods:
```yaml
kubernetes:
  provision_timeout: 0
  pod_config:
    metadata:
      labels:
        kueue.x-k8s.io/queue-name: user-queue
```

2. 🎉SkyPilot is ready to run on Kueue! Launch your SkyPilot clusters as usual. The pods will be scheduled by Kueue based on the queue name specified in the pod metadata.

💡**Hint** - you can use the `--down` flag to simulate a job-like interface that automatically releases resources after the job is done.

For example, if you configured a 9 CPU `nominalQuota` for your `ClusterQueue`, running the following two commands will launch the first job 
and then the second job will be queued until the first job is done:
```console
sky launch -c job1 --cloud kubernetes --cpus 6 --down -- sleep 60
sky launch -c job2 --cloud kubernetes --cpus 6 --down -- sleep 60
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

3. Use the `kueue.x-k8s.io/priority-class` label in the pod metadata to set the priority of the pod:
```yaml
kubernetes:
  provision_timeout: 0
  pod_config:
    metadata:
      labels:
        kueue.x-k8s.io/queue-name: user-queue
        kueue.x-k8s.io/priority-class: low-priority 
```

4. 🎉SkyPilot jobs will now run with priorities on Kueue!

To demonstrate an example, first we will run a job with `low-priority` that uses 8 CPUs, assuming a 9 CPU quota. To start, edit your `~/.sky/config` to use `low-priority`:
```yaml
...
kueue.x-k8s.io/priority-class: low-priority 
```

Then run a job with `low-priority` that uses 8 CPUs out of 9 CPU quota:
```console
sky launch -c job1 --cloud kubernetes --cpus 8 --down -- sleep 1200
```

Now edit your `~/.sky/config`to use `high-priority` for its jobs:
```yaml
...
kueue.x-k8s.io/priority-class: high-priority 
```

In a new terminal, launch the new job with that also uses 8 CPUs out of 9 CPU quota:
```console
sky launch -c job2 --cloud kubernetes --cpus 8 --down -- sleep 1200
```

You will see that the `low-priority` job will be preempted by the `high-priority` job.

To update your local SkyPilot cluster state, run `sky status --refresh`.

## ⚠️ Known Limitations
* If your SkyPilot cluster gets preempted, it will not be automatically rescheduled. You can get latest state with `sky status --refresh` and re-run failed clusters it with `sky launch` to re-run your job. We can look into supporting automatic rescheduling.
* The `queue-name` and `priority-class` configs apply globally to all tasks and the user must edit `~/.sky/config.yaml` to switch between queues and priorities. This can be painful, and we can look into supporting per-task queue and priority settings in the task YAML if needed.