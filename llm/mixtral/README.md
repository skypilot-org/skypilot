# Serving Mixtral from Mistral.ai

Mistral AI released Mixtral 8x7B, a high-quality sparse mixture of experts model (SMoE) with open weights. Mixtral outperforms Llama 2 70B on most benchmarks with 6x faster inference. Mistral.ai uses SkyPilot as [the default way](https://docs.mistral.ai/self-deployment/skypilot) to distribute their new model. This folder contains the code to serve Mixtral on any cloud with SkyPilot. 

There are three ways to serve the model:

## 1. Serve with a single instance

SkyPilot can help you serve Mixtral by automatically finding available resources on any cloud, provisioning the VM, opening the ports, and serving the model. To serve Mixtral with a single instance, run the following command:

```bash
sky launch -c mixtral ./serve.yaml
```

Note that we specify the following resources, so that SkyPilot will automatically find any of the available GPUs specified by automatically [failover](https://skypilot.readthedocs.io/en/latest/examples/auto-failover.html) through all the candidates (in the order of the prices):

```yaml
resources:
  accelerators: {A100:4, A100:8, A100-80GB:2, A100-80GB:4, A100-80GB:8}
```

### Accessing the model

We can now access the model through the OpenAI API with the IP and port:

```bash
IP=$(sky status --ip mixtral)

curl -L http://$IP:8000/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
      "model": "mistralai/Mixtral-8x7B-Instruct-v0.1",
      "prompt": "My favourite condiment is",
      "max_tokens": 25
  }'
```

## 2. Serve with multiple instances

When scaling up is required, [SkyServe](https://skypilot.readthedocs.io/en/latest/serving/sky-serve.html) is the library built on top of SkyPilot, which can help you scale up the serving with multiple instances, while still providing a single endpoint. To serve Mixtral with multiple instances, run the following command:

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

curl -L http://$ENDPOINT/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
      "model": "mistralai/Mixtral-8x7B-Instruct-v0.1",
      "prompt": "My favourite condiment is",
      "max_tokens": 25
  }'
```

## 3. Official guide from Mistral AI

Mistral.ai also includes a guide for launching the Mixtral 8x7B model with SkyPilot in their official doc. Please refer to [this link](https://docs.mistral.ai/self-deployment/skypilot) for more details.

> Note: the docker image of the official doc may not be updated yet, which can cause a failure where vLLM is complaining about the missing support for the model. Please feel free to create a new docker image with the setup commands in our [serve.yaml](./serve.yaml) file instead.
