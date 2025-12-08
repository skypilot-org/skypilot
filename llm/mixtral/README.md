<!-- $REMOVE -->
# Serving Mixtral from Mistral AI
<!-- $END_REMOVE -->
<!-- $UNCOMMENT# Mixtral: MOE LLM from Mistral AI -->

Mistral AI released Mixtral 8x7B, a high-quality sparse mixture of experts model (SMoE) with open weights. Mixtral outperforms Llama 2 70B on most benchmarks with 6x faster inference. Mistral AI uses SkyPilot as [the default way](https://docs.mistral.ai/deployment/self-deployment/skypilot) to distribute their new model. This folder contains the code to serve Mixtral on any cloud with SkyPilot.

There are three ways to serve the model:

## 1. Serve with a single instance

SkyPilot can help you serve Mixtral by automatically finding available resources on any cloud, provisioning the VM, opening the ports, and serving the model. To serve Mixtral with a single instance, run the following command:

```bash
sky launch -c mixtral ./serve.yaml
```

Note that we specify the following resources, so that SkyPilot will automatically find any of the available GPUs specified by automatically [failover](https://docs.skypilot.co/en/latest/examples/auto-failover.html) through all the candidates (in the order of the prices):

```yaml
resources:
  accelerators: {A100:4, A100:8, A100-80GB:2, A100-80GB:4, A100-80GB:8}
```

The following is the example output of the optimizer:

```
Considered resources (1 node):
----------------------------------------------------------------------------------------------------------
 CLOUD   INSTANCE                    vCPUs   Mem(GB)   ACCELERATORS   REGION/ZONE     COST ($)   CHOSEN
----------------------------------------------------------------------------------------------------------
 Azure   Standard_NC48ads_A100_v4    48      440       A100-80GB:2    eastus          7.35          ✔
 GCP     g2-standard-96              96      384       L4:8           us-east4-a      7.98
 GCP     a2-ultragpu-2g              24      340       A100-80GB:2    us-central1-a   10.06
 Azure   Standard_NC96ads_A100_v4    96      880       A100-80GB:4    eastus          14.69
 GCP     a2-highgpu-4g               48      340       A100:4         us-central1-a   14.69
 AWS     g5.48xlarge                 192     768       A10G:8         us-east-1       16.29
 GCP     a2-ultragpu-4g              48      680       A100-80GB:4    us-central1-a   20.11
 Azure   Standard_ND96asr_v4         96      900       A100:8         eastus          27.20
 GCP     a2-highgpu-8g               96      680       A100:8         us-central1-a   29.39
 Azure   Standard_ND96amsr_A100_v4   96      1924      A100-80GB:8    eastus          32.77
 AWS     p4d.24xlarge                96      1152      A100:8         us-east-1       32.77
 GCP     a2-ultragpu-8g              96      1360      A100-80GB:8    us-central1-a   40.22
 AWS     p4de.24xlarge               96      1152      A100-80GB:8    us-east-1       40.97
----------------------------------------------------------------------------------------------------------
```


### Accessing the model

We can now access the model through the OpenAI API with the IP and port:

```bash
IP=$(sky status --ip mixtral)

curl http://$IP:8000/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
      "model": "mistralai/Mixtral-8x7B-Instruct-v0.1",
      "prompt": "My favourite condiment is",
      "max_tokens": 25
  }'
```

Chat API is also supported:
```bash
IP=$(sky status --ip mixtral)

curl http://$IP:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
      "model": "mistralai/Mixtral-8x7B-Instruct-v0.1",
      "messages": [
        {
          "role": "user",
          "content": "Hello! What is your name?"
        }
      ],
      "max_tokens": 25
  }'
```

## 2. Serve with multiple instances

When scaling up is required, [SkyServe](https://docs.skypilot.co/en/latest/serving/sky-serve.html) is the library built on top of SkyPilot, which can help you scale up the serving with multiple instances, while still providing a single endpoint. To serve Mixtral with multiple instances, run the following command:

```bash
sky serve up -n mixtral ./serve.yaml
```

The additional arguments for serving specifies the way to check the healthiness of the service and manage the auto-restart of the service when unexpected failure happens:
```yaml
service:
  readiness_probe:
    path: /v1/chat/completions
    post_data:
      model: mistralai/Mixtral-8x7B-Instruct-v0.1
      messages:
        - role: user
          content: Hello! What is your name?
      max_tokens: 1
    initial_delay_seconds: 1200
  replica_policy:
    min_replicas: 1
```

Optional: To further save the cost by 3-4x, we can use the spot instances as the replicas, and SkyServe will automatically manage the spot instances, monitor the prices and preemptions, and restart the replica when needed.
To do so, we can add `use_spot: true` to the `resources` field, i.e.:
```yaml
resources:
  use_spot: true
  accelerators: {A100:4, A100:8, A100-80GB:2, A100-80GB:4, A100-80GB:8}
```

### Accessing the model

After the `sky serve up` command, there will be a single endpoint for the service. We can access the model through the OpenAI API with the IP and port:

```bash
ENDPOINT=$(sky serve status --endpoint mixtral)

curl http://$ENDPOINT/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
      "model": "mistralai/Mixtral-8x7B-Instruct-v0.1",
      "prompt": "My favourite condiment is",
      "max_tokens": 25
  }'
```

Chat API is also supported:
```bash
ENDPOINT=$(sky serve status --endpoint mixtral)

curl http://$ENDPOINT/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
      "model": "mistralai/Mixtral-8x7B-Instruct-v0.1",
      "messages": [
        {
          "role": "user",
          "content": "Hello! What is your name?"
        }
      ],
      "max_tokens": 25
  }'
```

## 3. Official guide from Mistral AI

Mistral AI also includes a guide for launching the Mixtral 8x7B model with SkyPilot in their official doc. Please refer to [this link](https://docs.mistral.ai/deployment/self-deployment/skypilot) for more details.

> Note: the docker image of the official doc may not be updated yet, which can cause a failure where vLLM is complaining about the missing support for the model. Please feel free to create a new docker image with the setup commands in our [serve.yaml](https://github.com/skypilot-org/skypilot/tree/master/llm/mixtral/serve.yaml) file instead.
