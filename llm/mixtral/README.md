# Serving Mixtral from Mistral.ai

Mistral AI released Mixtral 8x7B, a high-quality sparse mixture of experts model (SMoE) with open weights. Licensed under Apache 2.0. Mixtral outperforms Llama 2 70B on most benchmarks with 6x faster inference. This folder contains the code to serve Mixtral on any cloud with SkyPilot. There two ways to serve the model:

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

## 2. Serve with multiple instances

When scaling up is required, SkyServe is the library built on top of SkyPilot, which can help you scale up the serving with multiple instances, while still providing a single endpoint. To serve Mixtral with multiple instances, run the following command:

```bash
sky serve up -c mixtral ./serve.yaml
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
    auto_restart: true
```
