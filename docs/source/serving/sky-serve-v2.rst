.. _sky-serve-v2:

SkyServe v2: K8s-Native LLM Serving
====================================

.. note::

   SkyServe v2 is a new Kubernetes-native serving system that wraps
   `KServe <https://kserve.github.io/website/>`_,
   `llm-d <https://github.com/llm-d/llm-d>`_, and
   `KEDA <https://keda.sh/>`_ to provide production-grade LLM serving
   with a simple interface. It is currently in **alpha** and targets
   Kubernetes clusters only.

   The original SkyServe (see :ref:`sky-serve`) continues to work for
   VM-based and general-purpose serving.

SkyServe v2 takes a model ID and deploys it as a production-ready,
OpenAI-compatible inference endpoint on Kubernetes --- with KV cache-aware
routing, autoscaling on LLM metrics, and full Prometheus observability.

Why SkyServe v2?
----------------

SkyServe v2 addresses limitations of the original SkyServe for LLM workloads:

.. list-table::
   :header-rows: 1
   :widths: 20 40 40

   * - Problem
     - Original SkyServe
     - SkyServe v2
   * - **Routing**
     - Python FastAPI proxy (bottleneck)
     - KV cache-aware routing via llm-d + Gateway API
   * - **Autoscaling**
     - QPS-only
     - KV cache utilization, queue depth, tokens/sec via KEDA + Prometheus
   * - **Observability**
     - No metrics, must SSH to debug
     - Prometheus metrics (TTFT, TPOT, KV cache, GPU util), live in ``status``
   * - **Stability**
     - Single controller VM (SPOF)
     - K8s-native controllers (KServe, KEDA) with HA

Quick Start
-----------

Deploy a model with a single YAML line:

.. code-block:: yaml

    # serve.yaml
    model: Qwen/Qwen2.5-7B-Instruct

.. code-block:: console

    $ python -m sky.serve.skyserve_v2_cli up serve.yaml

SkyPilot automatically resolves the GPU type, count, tensor parallelism,
engine arguments, and autoscaling configuration. The model is deployed as a
vLLM pod with an OpenAI-compatible API.

Once deployed, check status:

.. code-block:: console

    $ python -m sky.serve.skyserve_v2_cli status

    SkyServe v2 Services (skyserve-v2)
    ======================================================================

      Service:  qwen2-5-7b-instruct
      Type:     Deployment
      Status:   READY
      Replicas: 1/1
      Pods:
        - qwen2-5-7b-instruct-...: Running (ready: 1/1, node: gpu-node-1, GPUs: 1)
      Metrics:
        KV Cache Usage:     0.0%
        Requests Running:   0
        Requests Waiting:   0
        Total Requests:     5
        Prompt Tokens:      185
        Generation Tokens:  46

Query the endpoint (via port-forward or in-cluster):

.. code-block:: console

    $ curl http://localhost:8000/v1/chat/completions \
        -H "Content-Type: application/json" \
        -d '{
          "model": "Qwen/Qwen2.5-7B-Instruct",
          "messages": [{"role": "user", "content": "Hello!"}],
          "max_tokens": 100
        }'

Tear down:

.. code-block:: console

    $ python -m sky.serve.skyserve_v2_cli down qwen2-5-7b-instruct


YAML Spec Reference
--------------------

SkyServe v2 supports four tiers of YAML specs, from minimal to full control.

Tier 1: Minimal (just a model)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: yaml

    model: meta-llama/Llama-3.1-70B-Instruct

SkyPilot auto-resolves GPU type, count, TP, engine args, replicas, and
autoscaling defaults.

Tier 2: With overrides
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: yaml

    model: meta-llama/Llama-3.1-70B-Instruct

    resources:
      accelerators: H100:2

    service:
      replicas: 2
      max_replicas: 8

Tier 3: Full control
~~~~~~~~~~~~~~~~~~~~

.. code-block:: yaml

    model: meta-llama/Llama-3.1-70B-Instruct
    engine: vllm
    engine_args:
      max_model_len: 32768
      enable_chunked_prefill: true

    resources:
      accelerators: A100:4

    service:
      name: my-llm-service
      replicas: 2
      max_replicas: 10

      routing:
        mode: kv_cache_aware        # round_robin | kv_cache_aware
        disaggregated: true         # Enable prefill-decode disaggregation

      autoscaling:
        metrics:
          - type: kv_cache_utilization
            target: 0.7
          - type: queue_depth
            target: 5
        upscale_delay: 60
        downscale_delay: 300

      prefill:
        resources:
          accelerators: H100:4
        replicas: 2

      lora_adapters:
        - name: my-fine-tune
          uri: hf://my-org/my-lora

Tier 4: Raw KServe passthrough
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: yaml

    kserve:
      apiVersion: serving.kserve.io/v1alpha1
      kind: LLMInferenceService
      spec:
        # ... raw LLMInferenceService spec, applied as-is


CLI Reference
-------------

.. code-block:: text

    python -m sky.serve.skyserve_v2_cli <command> [options]

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Command
     - Description
   * - ``up <yaml>``
     - Deploy a model service from a YAML spec
   * - ``status [name]``
     - Show status of deployed services with live Prometheus metrics
   * - ``down <name>``
     - Tear down a service and all its resources
   * - ``logs <name>``
     - View aggregated logs from all replicas
   * - ``endpoint <name>``
     - Print the service endpoint URL
   * - ``prereqs``
     - Check cluster prerequisites (KServe, KEDA, Prometheus, GPUs)
   * - ``generate <yaml>``
     - Dry-run: print generated K8s YAML without applying


Model Registry
--------------

SkyServe v2 includes a built-in registry that maps popular model IDs to
GPU requirements. For example:

.. list-table::
   :header-rows: 1
   :widths: 40 15 10 15

   * - Model
     - GPU
     - Count
     - TP
   * - ``meta-llama/Llama-3.1-8B-Instruct``
     - H100
     - 1
     - 1
   * - ``meta-llama/Llama-3.1-70B-Instruct``
     - H100
     - 4
     - 4
   * - ``meta-llama/Llama-3.1-405B-Instruct``
     - H100
     - 8
     - 8
   * - ``Qwen/Qwen2.5-7B-Instruct``
     - A100
     - 1
     - 1
   * - ``Qwen/Qwen2.5-72B-Instruct``
     - H100
     - 4
     - 4
   * - ``mistralai/Mixtral-8x7B-Instruct-v0.1``
     - H100
     - 2
     - 2
   * - ``deepseek-ai/DeepSeek-R1``
     - H100
     - 8
     - 8

For models not in the registry, SkyPilot infers the parameter count from
the model name (e.g., ``70B``) and computes GPU requirements automatically.
User overrides in the ``resources`` section always take precedence.


Autoscaling
-----------

SkyServe v2 uses `KEDA <https://keda.sh/>`_ to autoscale based on
vLLM Prometheus metrics. Supported trigger metrics:

.. list-table::
   :header-rows: 1
   :widths: 30 40 30

   * - Metric
     - vLLM Prometheus Metric
     - Typical Threshold
   * - ``kv_cache_utilization``
     - ``vllm:kv_cache_usage_perc``
     - 0.7 - 0.8
   * - ``queue_depth``
     - ``vllm:num_requests_waiting``
     - 3 - 10
   * - ``requests_per_second``
     - ``vllm:request_success_total`` (rate)
     - varies

Example autoscaling config:

.. code-block:: yaml

    service:
      replicas: 1         # min replicas
      max_replicas: 4

      autoscaling:
        metrics:
          - type: queue_depth
            target: 5
          - type: kv_cache_utilization
            target: 0.8
        downscale_delay: 300   # seconds before scaling down


Observability
-------------

SkyServe v2 automatically configures Prometheus scraping on deployed pods
(via ``prometheus.io/scrape`` annotations). The ``status`` command queries
Prometheus for live metrics:

- **KV Cache Usage**: GPU KV cache utilization percentage
- **Requests Running/Waiting**: Active and queued request counts
- **Total Requests**: Cumulative successful requests
- **Prompt/Generation Tokens**: Token throughput

These metrics are the same ones exposed by vLLM's ``/metrics`` endpoint
and can also be visualized in Grafana or any Prometheus-compatible dashboard.


Prerequisites
-------------

SkyServe v2 requires the following on the target Kubernetes cluster:

.. list-table::
   :header-rows: 1
   :widths: 20 20 60

   * - Component
     - Required?
     - Purpose
   * - GPU nodes
     - Yes
     - NVIDIA GPU nodes with ``nvidia.com/gpu`` resources
   * - cert-manager
     - Yes (for KServe)
     - TLS certificate management
   * - KServe v0.15+
     - Recommended
     - LLM serving control plane (``LLMInferenceService`` CRD)
   * - KEDA
     - Optional
     - Autoscaling on Prometheus metrics
   * - Prometheus
     - Optional
     - Metrics collection for observability and autoscaling

.. note::

   If KServe is not installed, SkyServe v2 falls back to a **direct
   deployment** mode that generates standard Kubernetes Deployment + Service
   resources. This mode supports the full OpenAI-compatible API but does not
   include KV cache-aware routing or prefill-decode disaggregation.

Run the prerequisite checker:

.. code-block:: console

    $ python -m sky.serve.skyserve_v2_cli prereqs

    SkyServe v2 Prerequisites
    ========================
      [OK] kubectl (v1.32)
      [OK] cluster (4 nodes)
      [OK] gpu_nodes (2 GPU nodes: H100_NVLINK_80GB)
      [OK] cert_manager (v1.16.3)
      [OK] kserve (v0.15.1)
      [MISSING] gateway_api
      [OK] keda
      [OK] prometheus


Architecture
------------

SkyServe v2 follows a **"wrap, don't build"** philosophy. Instead of
reimplementing load balancing, routing, and autoscaling, it generates
Kubernetes resource manifests and delegates to the cloud-native ecosystem:

.. code-block:: text

    sky serve up serve.yaml
        |
        +---> YAML Parser (4-tier spec)
        +---> Model Config Resolver (model ID -> GPU, TP, engine args)
        +---> K8s Resource Generator
        |       |---> LLMInferenceService (KServe) or Deployment+Service
        |       |---> KEDA ScaledObject (autoscaling)
        |       +---> HF token Secret
        +---> kubectl apply
                |
                v
        Kubernetes Cluster
          KServe ---> Pod lifecycle
          llm-d  ---> KV cache-aware routing
          KEDA   ---> Autoscaling on vLLM metrics
          vLLM   ---> Model inference + /metrics

For the full design document, see ``designs/skyserve_v2_design.md`` in the
repository.
