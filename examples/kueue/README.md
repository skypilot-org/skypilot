# Using Kueue with SkyPilot

1. Setup Kueue and [enable integration for v1/pod](https://kueue.sigs.k8s.io/docs/tasks/run/plain_pods/#before-you-begin). The following is an example enabling pod integration in `controller_manager_config.yaml` in the Kueue installation manifest:

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
      # Kueue uses podSelector to manage pods with particular 
      # labels. The default podSelector will match all the pods. 
      podSelector:
        matchExpressions:
          - key: kueue-job
            operator: In
            values: [ "true", "True", "yes" ]
```
2. Setup the `ClusterQueue` and `LocalQueue` for Kueue. Make sure `nvidia.com/gpu` is available in the resource flavor and under `coveredResources` in the `ClusterQueue` CR.
```console
kubectl apply -f single-clusterqueue-setup.yaml
```

3. Update your SkyPilot config YAML to use the Kueue scheduler by using `kueue.x-k8s.io/queue-name` label in the pod metadata. Additionally, set `provision_timeout: 0` to let jobs queue indefinitely. For example, the following config will use the `user-queue` queue for scheduling the pods:
```yaml
kubernetes:
  provision_timeout: 0
  pod_config:
    metadata:
      labels:
        kueue.x-k8s.io/queue-name: user-queue
```

4. 🎉SkyPilot is ready to go! Launch your SkyPilot clusters as usual. The pods will be scheduled by Kueue based on the queue name specified in the pod metadata.

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

Use `sky status --refresh` to get the updated status of the SkyPilot clusters.

## Caveats
* Currently if your pod gets evicted, the job will not be rescheduled. You can re-run it with `sky launch` to reschedule the job. We can look into supporting automatic rescheduling.
* TODO - Add priority examples.