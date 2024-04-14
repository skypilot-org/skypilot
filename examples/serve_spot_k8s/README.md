# SkyServe on autoscaling K8s + burst to cloud spot instances

This example demonstrates how to deploy Llama2 inference endpoint on an autoscaling Kubernetes cluster, and then burst to spot instances on the cloud(s) when the Kubernetes cluster is at full capacity.

**In this example, we will:**
1. Create a Kubernetes cluster (GKE) with 1 node that can autoscale to upto 2 nodes, with 1x T4 GPU per node
2. Deploy a Llama2 inference endpoint which is to be served by 3 replicas, each requesting 1x T4 GPU
3. Observe the autoscaling behavior of the Kubernetes cluster and the bursting to cloud spot instances by SkyServe

**We will observe:**
1. SkyServe will first deploy 1 replica on the available GPU on the Kubernetes cluster
2. After a few minutes, the underlying Kubernetes cluster will autoscale to 2 nodes and the second replica will be deployed on the same Kubernetes cluster. However, the third replica will stay pending as the Kubernetes cluster is at full capacity
3. When it detects the Kubernetes cluster is no longer autoscaling, SkyServe will start a spot instance on GCP on an available region to deploy the third replica.
4. All incoming queries will be transparently load-balanced across the 3 replicas

![SkyServe on autoscaling K8s + burst to cloud spot instances](https://i.imgur.com/xVGDHVg.jpeg)

**Outcome - you get one common endpoint to run all your queries, while SkyServe manages the underlying infrastructure (K8s + Clouds) for you:**
```console
$ sky status
Clusters
NAME                           LAUNCHED  RESOURCES                                                                  STATUS  AUTOSTOP    COMMAND
sky-serve-controller-2ea485ea  1 hr ago  1x Kubernetes(4CPU--4GB, cpus=4+, disk_size=200, ports=['30001-30020']...  UP      10m (down)  sky serve up -n llama2 ll...

Managed spot jobs
No in-progress spot jobs. (See: sky spot -h)

Services
NAME    VERSION  UPTIME   STATUS  REPLICAS  ENDPOINT
llama2  1        34m 44s  READY   3/3       35.225.61.44:30001

Service Replicas
SERVICE_NAME  ID  VERSION  ENDPOINT                   LAUNCHED  RESOURCES                 STATUS  REGION
llama2        1   1        http://34.173.84.219:8888  1 hr ago  1x Kubernetes({'T4': 1})  READY   kubernetes
llama2        2   1        http://35.199.51.206:8888  1 hr ago  1x GCP([Spot]{'T4': 1})   READY   us-east4
llama2        3   1        http://34.31.108.35:8888   1 hr ago  1x Kubernetes({'T4': 1})  READY   kubernetes

* To see detailed service status: sky serve status -a
* 1 cluster has auto{stop,down} scheduled. Refresh statuses with: sky status --refresh
```

<details>
<summary>Click to see example running curl on the common endpoint</summary>

```console
curl -L http://35.225.61.44:30001/v1/chat/completions \
 -H "Content-Type: application/json" \
 -d '{
       "model": "llama2",
       "messages": [
         {
           "role": "system",
           "content": "You are a helpful assistant."
         },
         {
           "role": "user",
           "content": "Who are you?"
         }
       ]
     }'
     
{"id":"chatcmpl-915","object":"chat.completion","created":1713055919,"model":"llama2","system_fingerprint":"fp_ollama","choices":[{"index":0,"message":{"role":"assistant","content":"Hello there! *adjusts glasses* I'm just an AI, here to help you with any questions or tasks you may have. My name is Assistant, but you can call me Assit for short. I'm a friendly and efficient assistant, always ready to lend a helping hand. How may I assist you today?"},"finish_reason":"stop"}],"usage":{"prompt_tokens":22,"completion_tokens":73,"total_tokens":95}}
```
</details>

## How to launch

Create the Kubernetes cluster with autoscaling enabled. This cluster has the following configuration:
- Starts with 1 node, can autoscale to upto 2 nodes
- 1x T4 GPU and 16 vCPUs per node

```console
gcloud beta container --project "skypilot-375900" clusters create "sky-serve" --no-enable-basic-auth --cluster-version "1.27.8-gke.1067004" --release-channel "regular" --machine-type "n1-standard-16" --accelerator "type=nvidia-tesla-t4,count=1" --image-type "COS_CONTAINERD" --disk-type "pd-balanced" --disk-size "100" --metadata disable-legacy-endpoints=true --scopes "https://www.googleapis.com/auth/devstorage.read_only","https://www.googleapis.com/auth/logging.write","https://www.googleapis.com/auth/monitoring","https://www.googleapis.com/auth/servicecontrol","https://www.googleapis.com/auth/service.management.readonly","https://www.googleapis.com/auth/trace.append" --num-nodes "1" --logging=SYSTEM,WORKLOAD --monitoring=SYSTEM --enable-ip-alias --network "projects/skypilot-375900/global/networks/default" --subnetwork "projects/skypilot-375900/regions/us-central1/subnetworks/default" --no-enable-intra-node-visibility --default-max-pods-per-node "110" --security-posture=standard --workload-vulnerability-scanning=disabled --no-enable-master-authorized-networks --addons HorizontalPodAutoscaling,HttpLoadBalancing,GcePersistentDiskCsiDriver --enable-autoupgrade --enable-autorepair --max-surge-upgrade 1 --max-unavailable-upgrade 0 --binauthz-evaluation-mode=DISABLED --enable-managed-prometheus --enable-shielded-nodes --node-locations "us-central1-c" --enable-autoscaling --min-nodes=1 --max-nodes=2 --zone "us-central1-c"
```

Copy over the recommended sky config.yaml:
```console
cp config.yaml ~/.sky/config.yaml
```

Launch the SkyServe service:
```console
sky serve up -n llama2 llama2.yaml
```

<details>
<summary>Click to see the output</summary>

```console
Service from YAML spec: llama2.yaml
Service Spec:
Readiness probe method:           POST /v1/chat/completions {"model": "llama2", "messages": [{"role": "user", "content": "Hello! What is your name?"}], "max_tokens": 1}
Readiness initial delay seconds:  1800
Replica autoscaling policy:       Fixed 3 replicas
Spot Policy:                      No spot policy

Each replica will use the following resources (estimated):
== Optimizer ==
Estimated cost: $0.0 / hour

Considered resources (1 node):
------------------------------------------------------------------------------------------------------------------
 CLOUD        INSTANCE                     vCPUs   Mem(GB)   ACCELERATORS   REGION/ZONE       COST ($)   CHOSEN
------------------------------------------------------------------------------------------------------------------
 Kubernetes   4CPU--8GB--1T4               4       8         T4:1           kubernetes        0.00          ✔
 Azure        Standard_NC4as_T4_v3[Spot]   4       28        T4:1           eastus            0.05
 AWS          g4dn.xlarge[Spot]            4       16        T4:1           ap-northeast-3c   0.13
 GCP          n1-standard-4[Spot]          4       15        T4:1           us-east4-a        0.15
 AWS          g4dn.xlarge                  4       16        T4:1           us-east-1         0.53
 Azure        Standard_NC4as_T4_v3         4       28        T4:1           eastus            0.53
 GCP          n1-standard-4                4       15        T4:1           us-central1-a     0.54
------------------------------------------------------------------------------------------------------------------

Launching a new service 'llama2'. Proceed? [Y/n]:
```

</details>

## Observing the autoscaling behavior
You will see initial status when the service is just started:
```console
(base) ➜  ~ sky status
Clusters
NAME                           LAUNCHED  RESOURCES                                                                  STATUS  AUTOSTOP    COMMAND
sky-serve-controller-2ea485ea  1 hr ago  1x Kubernetes(4CPU--4GB, cpus=4+, disk_size=200, ports=['30001-30020']...  UP      10m (down)  sky serve up -n llama2 ll...

Managed spot jobs
No in-progress spot jobs. (See: sky spot -h)

Services
NAME    VERSION  UPTIME  STATUS      REPLICAS  ENDPOINT
llama2  -        -       NO_REPLICA  0/3       35.225.61.44:30001

Service Replicas
SERVICE_NAME  ID  VERSION  ENDPOINT  LAUNCHED  RESOURCES                 STATUS        REGION
llama2        1   1        -         1 hr ago  1x Kubernetes({'T4': 1})  PROVISIONING  kubernetes
llama2        2   1        -         1 hr ago  1x Kubernetes({'T4': 1})  PROVISIONING  kubernetes
llama2        3   1        -         1 hr ago  1x Kubernetes({'T4': 1})  PROVISIONING  kubernetes

* To see detailed service status: sky serve status -a
* 1 cluster has auto{stop,down} scheduled. Refresh statuses with: sky status --refresh
```

The Kubernetes cluster will start autoscaling to 2 nodes:
```console
(base) ➜  ~ k get nodes
NAME                                      STATUS   ROLES    AGE     VERSION
gke-skyserve-default-pool-848cc5b6-3bkd   Ready    <none>   41m     v1.27.8-gke.1067004
gke-skyserve-default-pool-848cc5b6-k84z   Ready    <none>   3m28s   v1.27.8-gke.1067004
```

Two replicas get provisioned on Kubernetes, and the third starts getting scheduled on the cloud spot instances
```console
~ sky status
Clusters
NAME                           LAUNCHED  RESOURCES                                                                  STATUS  AUTOSTOP    COMMAND
sky-serve-controller-2ea485ea  1 hr ago  1x Kubernetes(4CPU--4GB, cpus=4+, disk_size=200, ports=['30001-30020']...  UP      10m (down)  sky serve up -n llama2 ll...

Managed spot jobs
No in-progress spot jobs. (See: sky spot -h)

Services
NAME    VERSION  UPTIME   STATUS  REPLICAS  ENDPOINT
llama2  1        11m 36s  READY   2/3       35.225.61.44:30001

Service Replicas
SERVICE_NAME  ID  VERSION  ENDPOINT                   LAUNCHED  RESOURCES                  STATUS        REGION
llama2        1   1        http://34.173.84.219:8888  1 hr ago  1x Kubernetes({'T4': 1})   READY         kubernetes
llama2        2   1        http://35.199.51.206:8888  1 hr ago  1x GCP([Spot]{'T4': 1})    STARTING      us-east4
llama2        3   1        http://34.31.108.35:8888   1 hr ago  1x Kubernetes({'T4': 1})   READY         kubernetes

* To see detailed service status: sky serve status -a
* 1 cluster has auto{stop,down} scheduled. Refresh statuses with: sky status --refresh```
```

Eventually, all replicas will be up and running:
```console
sky status
Clusters
NAME                           LAUNCHED  RESOURCES                                                                  STATUS  AUTOSTOP    COMMAND
sky-serve-controller-2ea485ea  1 hr ago  1x Kubernetes(4CPU--4GB, cpus=4+, disk_size=200, ports=['30001-30020']...  UP      10m (down)  sky serve up -n llama2 ll...

Managed spot jobs
No in-progress spot jobs. (See: sky spot -h)

Services
NAME    VERSION  UPTIME   STATUS  REPLICAS  ENDPOINT
llama2  1        34m 44s  READY   3/3       35.225.61.44:30001

Service Replicas
SERVICE_NAME  ID  VERSION  ENDPOINT                   LAUNCHED  RESOURCES                 STATUS  REGION
llama2        1   1        http://34.173.84.219:8888  1 hr ago  1x Kubernetes({'T4': 1})  READY   kubernetes
llama2        2   1        http://35.199.51.206:8888  1 hr ago  1x GCP([Spot]{'T4': 1})   READY   us-east4
llama2        3   1        http://34.31.108.35:8888   1 hr ago  1x Kubernetes({'T4': 1})  READY   kubernetes

* To see detailed service status: sky serve status -a
* 1 cluster has auto{stop,down} scheduled. Refresh statuses with: sky status --refresh
```

We can then send requests to the common endpoint:
```console
$ curl -L http://35.225.61.44:30001/v1/chat/completions \
 -H "Content-Type: application/json" \
 -d '{
       "model": "llama2",
       "messages": [
         {
           "role": "system",
           "content": "You are a helpful assistant."
         },
         {
           "role": "user",
           "content": "Who are you?"
         }
       ]
     }'

{
  "id": "chatcmpl-915",
  "object": "chat.completion",
  "created": 1713055919,
  "model": "llama2",
  "system_fingerprint": "fp_ollama",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Hello there! *adjusts glasses* I'm just an AI, here to help you with any questions or tasks you may have. My name is Assistant, but you can call me Assit for short. I'm a friendly and efficient assistant, always ready to lend a helping hand. How may I assist you today?"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 22,
    "completion_tokens": 73,
    "total_tokens": 95
  }
}
```
