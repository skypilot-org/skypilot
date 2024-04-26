# SkyServe on K8s with bursting to spot instances across regions

This example demonstrates how to deploy a Llama 3 inference endpoint on a Kubernetes cluster to serve a baseline query rate, and burst to using spot instances on the cloud(s) across regions when more replicas are required.

## Why SkyServe?

**What's different from using k8s node pools with Spot instances?**

Autoscaling node groups on GKE (and other hosted k8s offerings) operate within just one region. This can be limiting because:
1. 📉 **Capacity** - using a single region limits the number of replicas you can deploy because of capacity constraints from the cloud provider. 
2. ❌ **Correlated failures** - spot instances in a single region are affected by correlated failures. I.e., if a spot instance is terminated in one region, it is likely that other spot instances in the same region will also be terminated. Spreading across regions mitigates this risk. 
3. 🔐 **Lock-in** - you are locked into a single cloud provider and region, reducing your ability to optimize costs and improve availability.

**How SkyServe solves these problems**
1. 🌐 SkyServe provides a **common endpoint** to run all your queries, while managing the underlying infrastructure (K8s + Clouds) for you.
2. 💸 SkyServe can **burst to using spot instances** across regions, and across cloud providers, to ensure high availability and lowest costs.
3. 💪🏻 SkyServe **transparently recovers** from spot instance terminations and service failures, ensuring your service is always available. 

## Example - running Llama3 across K8s and spot instances

**In this example, we will:**
1. Create a Kubernetes cluster (GKE) with 3 nodes, with 1x T4 GPU per node.
2. Deploy a Llama3 inference endpoint which is to be served by 4 replicas, out of which 2 must be on the Kubernetes cluster and 2 on cloud spot instances. 
3. Observe the autoscaling behavior of SkyServe - it will serve the baseline load with 2 replicas on the Kubernetes cluster, and autoscale up to 2 more replicas on cloud spot instances when the incoming query rate spikes.

**We will observe:**
1. SkyServe will first deploy 2 replicas on the Kubernetes cluster to serve the baseline load.
2. After a few minutes, we will increase the load on the service by sending more queries to the service. This will trigger SkyServe to autoscale the service to more replicas, this time using spot instances.
3. All incoming queries will be transparently load-balanced across the 4 replicas

<!-- Source: https://docs.google.com/drawings/d/146hDLsAcKbCL-0ZhW6-Co2rkpK9028Wzwm1m7kEIrWg/edit?usp=sharing -->
![SkyServe on K8s + burst to cloud spot instances](https://i.imgur.com/xDoA94y.jpeg)

**Outcome - you get one common endpoint to run all your queries, while SkyServe manages autoscaling underlying infrastructure (K8s + Spot) for you:**
```console
$ sky status
Clusters
NAME                           LAUNCHED  RESOURCES                                                                  STATUS  AUTOSTOP    COMMAND
sky-serve-controller-2ea485ea  1 hr ago  1x Kubernetes(4CPU--4GB, cpus=4+, disk_size=200, ports=['30001-30020']...  UP      10m (down)  sky serve up -n llama3 ll...

Managed spot jobs
No in-progress spot jobs. (See: sky spot -h)

Services
NAME    VERSION  UPTIME   STATUS      REPLICAS  ENDPOINT
llama3  -        54m 17s  READY       4/4       35.194.42.249:30001

Service Replicas
SERVICE_NAME  ID  VERSION  ENDPOINT                    LAUNCHED     RESOURCES                 STATUS        REGION
llama3        1   1        http://35.238.153.130:8888  55 mins ago  1x Kubernetes({'T4': 1})  READY         kubernetes
llama3        2   1        http://35.193.203.168:8888  56 mins ago  1x Kubernetes({'T4': 1})  READY         kubernetes
llama3        3   1        http://34.85.222.215:8888   7 min ago    1x GCP([Spot]{'T4': 1})   READY         us-east4
llama3        4   1        http://34.48.95.97:8888     7 min ago    1x GCP([Spot]{'T4': 1})   READY         us-east4

* To see detailed service status: sky serve status -a
* 1 cluster has auto{stop,down} scheduled. Refresh statuses with: sky status --refresh
```

Here is an example curl command to send a query to the service using the common endpoint:

```console
curl -L http://35.225.61.44:30001/v1/chat/completions \
 -H "Content-Type: application/json" \
 -d '{
       "model": "llama3",
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
     
{"id":"chatcmpl-915","object":"chat.completion","created":1713055919,"model":"llama3","system_fingerprint":"fp_ollama","choices":[{"index":0,"message":{"role":"assistant","content":"Hello there! *adjusts glasses* I'm just an AI, here to help you with any questions or tasks you may have. My name is Assistant, but you can call me Assit for short. I'm a friendly and efficient assistant, always ready to lend a helping hand. How may I assist you today?"},"finish_reason":"stop"}],"usage":{"prompt_tokens":22,"completion_tokens":73,"total_tokens":95}}
```

## How to run the example

Check out this branch and install SkyPilot from source:
```console
git clone https://github.com/skypilot-org/skypilot.git
cd skypilot
git checkout k8s_serve_spot_example
pip install -e ".[kubernetes,gcp]"
```

Create the Kubernetes cluster on GKE. This cluster has the following configuration:
- Has 3 nodes
- 1x T4 GPU and 16 vCPUs per node

```console
PROJECT_ID=$(gcloud config get-value project)
CLUSTER_NAME=gkeusc5
gcloud beta container --project "${PROJECT_ID}" clusters create "${CLUSTER_NAME}" --zone "us-central1-c" --no-enable-basic-auth --cluster-version "1.27.12-gke.1115000" --release-channel "regular" --machine-type "n1-standard-16" --accelerator "type=nvidia-tesla-t4,count=1" --image-type "COS_CONTAINERD" --disk-type "pd-balanced" --disk-size "100" --metadata disable-legacy-endpoints=true --scopes "https://www.googleapis.com/auth/devstorage.read_only","https://www.googleapis.com/auth/logging.write","https://www.googleapis.com/auth/monitoring","https://www.googleapis.com/auth/servicecontrol","https://www.googleapis.com/auth/service.management.readonly","https://www.googleapis.com/auth/trace.append" --num-nodes "3" --logging=SYSTEM,WORKLOAD --monitoring=SYSTEM --enable-ip-alias --network "projects/${PROJECT_ID}/global/networks/default" --subnetwork "projects/${PROJECT_ID}/regions/us-central1/subnetworks/default" --no-enable-intra-node-visibility --default-max-pods-per-node "110" --security-posture=standard --workload-vulnerability-scanning=disabled --no-enable-master-authorized-networks --addons HorizontalPodAutoscaling,HttpLoadBalancing,GcePersistentDiskCsiDriver --enable-autoupgrade --enable-autorepair --max-surge-upgrade 1 --max-unavailable-upgrade 0 --enable-managed-prometheus --enable-shielded-nodes --node-locations "us-central1-c"
```

Copy over the recommended sky config.yaml:
```console
cp config.yaml ~/.sky/config.yaml
```

Launch the SkyServe service:
```console
sky serve up -n llama3 llama3.yaml
```

<details>
<summary>Click to see the output</summary>

This will show only the spot instance resources considered for the service, even though the service will be launched with 2 replicas on the Kubernetes cluster and 2 replicas on spot instances.

```console
Service from YAML spec: llama3.yaml
Service Spec:
Readiness probe method:           POST /v1/chat/completions {"model": "llama3", "messages": [{"role": "user", "content": "Hello! What is your name?"}], "max_tokens": 1}
Readiness initial delay seconds:  1800
Replica autoscaling policy:       Autoscaling from 2 to 4 replicas (target QPS per replica: 1)
Spot Policy:                      Static spot mixture with 2 base on-demand replicas

Each replica will use the following resources (estimated):
== Optimizer ==
Estimated cost: $0.0 / hour

Considered resources (1 node):
------------------------------------------------------------------------------------------------------
 CLOUD   INSTANCE              vCPUs   Mem(GB)   ACCELERATORS   REGION/ZONE       COST ($)   CHOSEN   
------------------------------------------------------------------------------------------------------
 AWS     g4dn.xlarge[Spot]     4       16        T4:1           ap-northeast-3b   0.14          ✔     
 GCP     n1-standard-4[Spot]   4       15        T4:1           us-east4-a        0.15                
------------------------------------------------------------------------------------------------------


Launching a new service 'llama3'. Proceed? [Y/n]:
```

</details>

## Observing the autoscaling behavior
You will see initial status when the service is just started:
```console
$ sky status
Clusters
NAME                           LAUNCHED     RESOURCES                                                                  STATUS  AUTOSTOP    COMMAND
sky-serve-controller-2ea485ea  23 mins ago  1x Kubernetes(4CPU--4GB, cpus=4+, disk_size=200, ports=['30001-30020']...  UP      10m (down)  sky serve up -n llama3 ll...

Managed spot jobs
No in-progress spot jobs. (See: sky spot -h)

Services
NAME    VERSION  UPTIME   STATUS  REPLICAS  ENDPOINT
llama3  1        16m 26s  READY   2/2       35.194.42.249:30001

Service Replicas
SERVICE_NAME  ID  VERSION  ENDPOINT                    LAUNCHED     RESOURCES                 STATUS  REGION
llama3        1   1        http://35.238.153.130:8888  17 mins ago  1x Kubernetes({'T4': 1})  READY   kubernetes
llama3        2   1        http://35.193.203.168:8888  17 mins ago  1x Kubernetes({'T4': 1})  READY   kubernetes

* To see detailed service status: sky serve status -a
* 1 cluster has auto{stop,down} scheduled. Refresh statuses with: sky status --refresh
```

The Kubernetes cluster is hosting 2 replicas. You can send it a query using the provided query generation script:
```console
$ ./query_generator.sh 35.194.42.249:30001 0.1 # 0.1 QPS - Query once every 10 seconds              
Sending a query to 35.194.42.249:30001
{"id":"chatcmpl-515","object":"chat.completion","created":1714085677,"model":"llama3","system_fingerprint":"fp_ollama","choices":[{"index":0,"message":{"role":"assistant","content":"I'm just an AI, a computer program designed to simulate conversations and answer questions to the best of my ability. I'm here to help with any topic or problem you'd like to discuss. I don't have personal feelings or opinions, but I'm always happy to provide information, guidance, or just listen if you need someone to talk to. How can I assist you today?"},"finish_reason":"stop"}],"usage":{"prompt_tokens":25,"completion_tokens":78,"total_tokens":103}}
```

### Forcing autoscaling by increasing the query rate

You can increase the query rate to force autoscaling. In this example, we will increase the query rate to 4 QPS:
```console
$ ./query_generator.sh 35.194.42.249:30001 4
```

After 5 minutes, SkyPilot will trigger autoscaling. You can [configure the autoscaling delay](https://skypilot.readthedocs.io/en/latest/serving/autoscaling.html#scaling-delay) in the service spec. 
If you run `sky status`, you will observe that more replicas start getting scheduled on the cloud spot instances.
```console
$ sky status
Clusters
NAME                           LAUNCHED     RESOURCES                                                                  STATUS  AUTOSTOP    COMMAND
sky-serve-controller-2ea485ea  56 mins ago  1x Kubernetes(4CPU--4GB, cpus=4+, disk_size=200, ports=['30001-30020']...  UP      10m (down)  sky serve up -n llama3 ll...

Managed spot jobs
No in-progress spot jobs. (See: sky spot -h)

Services
NAME    VERSION  UPTIME   STATUS      REPLICAS  ENDPOINT
llama3  -        48m 57s  READY       2/4       35.194.42.249:30001

Service Replicas
SERVICE_NAME  ID  VERSION  ENDPOINT                    LAUNCHED     RESOURCES                 STATUS        REGION
llama3        1   1        http://35.238.153.130:8888  49 mins ago  1x Kubernetes({'T4': 1})  READY         kubernetes
llama3        2   1        http://35.193.203.168:8888  50 mins ago  1x Kubernetes({'T4': 1})  READY         kubernetes
llama3        3   1        -                           1 min ago    1x GCP([Spot]{'T4': 1})   PROVISIONING  us-east4
llama3        4   1        -                           1 min ago    1x GCP([Spot]{'T4': 1})   PROVISIONING  us-east4

* To see detailed service status: sky serve status -a
* 1 cluster has auto{stop,down} scheduled. Refresh statuses with: sky status --refresh
```

Eventually, all replicas will be up and running:
```console
sky status
Clusters
NAME                           LAUNCHED  RESOURCES                                                                  STATUS  AUTOSTOP    COMMAND
sky-serve-controller-2ea485ea  1 hr ago  1x Kubernetes(4CPU--4GB, cpus=4+, disk_size=200, ports=['30001-30020']...  UP      10m (down)  sky serve up -n llama3 ll...

Managed spot jobs
No in-progress spot jobs. (See: sky spot -h)

Services
NAME    VERSION  UPTIME   STATUS      REPLICAS  ENDPOINT
llama3  -        54m 17s  READY       4/4       35.194.42.249:30001

Service Replicas
SERVICE_NAME  ID  VERSION  ENDPOINT                    LAUNCHED     RESOURCES                 STATUS        REGION
llama3        1   1        http://35.238.153.130:8888  55 mins ago  1x Kubernetes({'T4': 1})  READY         kubernetes
llama3        2   1        http://35.193.203.168:8888  56 mins ago  1x Kubernetes({'T4': 1})  READY         kubernetes
llama3        3   1        http://34.85.222.215:8888   7 min ago    1x GCP([Spot]{'T4': 1})   READY         us-east4
llama3        4   1        http://34.48.95.97:8888     7 min ago    1x GCP([Spot]{'T4': 1})   READY         us-east4

* To see detailed service status: sky serve status -a
* 1 cluster has auto{stop,down} scheduled. Refresh statuses with: sky status --refresh
```

## Summary
In this example, we demonstrated how to deploy a Llama3 inference endpoint on a fixed set of Kubernetes nodes, and autoscale to cloud spot instances across regions when the query rate increases. This allows you to serve a baseline query rate with a fixed set of resources, and burst to using spot instances across regions when more replicas are required.

Using this approach you got:
1. **💸 ~35% cost savings**: Instead of running 4 on-demand T4 instances on GCP, which would have cost $2.34/hr, you are running 2 on-demand instances and 2 spot instances, which cost $1.54/hr.
2. **⚙️ High availability**: By bursting to spot instances across regions, you are reducing the risk of correlated failures and ensuring high availability of your service. If service failures occur, SkyPilot will transparently recover from them.
3. **⚖️ Transparent autoscaling**: SkyServe transparently autoscales your service to more replicas when the query rate increases, and scales down when the query rate decreases.
4. **🌎 Common endpoint**: You get one common endpoint to run all your queries, while SkyServe manages autoscaling underlying infrastructure (K8s + Spot) for you.
