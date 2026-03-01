# SkyServe v2: K8s-Native LLM Serving via KServe + llm-d

**Status:** Phase 2 Implemented & Validated (Auto-Install, LLMInferenceService, PD Disaggregation, Cross-Cluster Metrics)
**Date:** 2026-02-28 (Phase 2) | 2026-02-07 (Phase 1)

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Landscape: The Cloud-Native Quartet](#2-landscape-the-cloud-native-quartet)
3. [Decision: Wrap, Don't Build](#3-decision-wrap-dont-build)
4. [Architecture](#4-architecture)
5. [User-Facing Spec](#5-user-facing-spec)
6. [Implementation Plan](#6-implementation-plan)
7. [Phase 1 Implementation Results](#7-phase-1-implementation-results)
8. [Phase 2 Implementation Results](#8-phase-2-implementation-results)
9. [Open Questions](#9-open-questions)
10. [Sources](#10-sources)

---

## 1. Problem Statement

Current SkyServe has four core problems:

| Problem | Details |
|---------|---------|
| **Stability** | Entire control plane (LB, autoscaler, replica manager) runs as custom Python on a single controller VM. Tightly coupled; a bug in any component can take down the whole service. |
| **Low Performance** | Python FastAPI proxy forwards all traffic (~100 concurrent connections, 120s timeout). No KV cache awareness, no prefix sharing, no PD disaggregation. |
| **Inflexible Autoscaling** | QPS-only (measured by SkyPilot LB). No support for engine metrics (KV cache util, queue depth, tokens/sec) or Prometheus. |
| **No Observability** | No dashboards, no per-replica metrics, no alerting. Must SSH into controller to debug. |

---

## 2. Landscape: The Cloud-Native Quartet

As of early 2026, the K8s LLM inference stack has converged into four complementary layers:

| Layer | Component | What It Does |
|-------|-----------|-------------|
| **Gateway** | [Gateway API Inference Extension](https://gateway-api-inference-extension.sigs.k8s.io/) (GIE) | Unified entry point. InferencePool (v1, stable) + InferenceModel CRDs. Model-aware routing via Envoy ext-proc + Body-Based Router. Supported by Istio 1.27+. |
| **Control Plane** | [KServe](https://kserve.github.io/website/) v0.15+ | Lifecycle management and CRDs. New **`LLMInferenceService`** CRD (v1alpha1) purpose-built for LLMs. Integrates KEDA for LLM autoscaling. |
| **Scheduling** | [llm-d](https://github.com/llm-d/llm-d) v0.5.0 | KV cache-aware routing (57x TTFT improvement claimed), prefill-decode disaggregation, hierarchical KV cache (GPU/CPU/NVMe via LMCache), variant autoscaling. 428 commits, 91 contributors, Apache 2.0. |
| **Execution** | vLLM | Inference engine. PagedAttention, continuous batching, OpenAI-compatible API. |

**Key finding**: KServe's `LLMInferenceService` is built directly on llm-d. They are tightly integrated -- KServe IS the control plane for llm-d, not a separate competing project. The `spec.router.scheduler` field in LLMInferenceService creates the llm-d inference scheduler automatically.

### What KServe + llm-d Already Provides

| Feature | Supported? | Mechanism |
|---------|-----------|-----------|
| KV cache-aware routing | **Yes** | llm-d inference scheduler + GIE Endpoint Picker (EPP) |
| Prefill-decode disaggregation | **Yes** | `spec.prefill` field in LLMInferenceService |
| Distributed KV cache | **Yes** | LMCache integration (Redis, LMCache server, ValKey, InfiniStore) |
| LLM-specific autoscaling | **Yes** | KEDA + vLLM Prometheus metrics (KV cache util, queue depth, etc.) |
| Multi-node inference | **Yes** | LeaderWorkerSet for pipeline/tensor parallelism |
| LoRA adapter management | **Yes** | `spec.model.lora` in LLMInferenceService |
| Expert parallelism (MoE) | **Yes** | DP+EP for Mixtral, DeepSeek-R1 etc. |
| Model-aware routing | **Yes** | GIE InferenceModel + Body-Based Router |
| Traffic splitting / canary | **Yes** | GIE InferenceModel supports traffic weights |
| Prometheus metrics | **Yes** | vLLM `/metrics` endpoint natively |
| Pre-built dashboards | **No** | Prometheus metrics exist, but no turnkey dashboards |
| Multi-cluster | **No** | Single K8s cluster only |
| Cost optimization | **No** | No GPU pricing awareness |
| Simplified "just a model" UX | **No** | Still requires full K8s resource spec (image, GPU limits, etc.) |

### LLMInferenceService CRD (KServe v0.15+)

```yaml
apiVersion: serving.kserve.io/v1alpha1
kind: LLMInferenceService
metadata:
  name: llama-3-8b
spec:
  model:
    uri: hf://meta-llama/Llama-3.1-8B-Instruct
    name: meta-llama/Llama-3.1-8B-Instruct
    criticality: Standard               # Scheduler prioritization
    lora:                                # Optional LoRA adapters
      - name: my-adapter
        uri: hf://my-org/my-lora
  replicas: 3
  parallelism:
    tensorParallelSize: 2
    dataParallelSize: 1
  template:                              # Decode pod spec
    containers:
      - name: main
        image: vllm/vllm-openai:latest
        resources:
          limits:
            nvidia.com/gpu: "2"
  prefill:                               # Optional: disaggregated prefill
    replicas: 2
    template:
      containers:
        - name: main
          image: vllm/vllm-openai:latest
          resources:
            limits:
              nvidia.com/gpu: "4"
  router:
    gateway: {}                          # Managed gateway
    route: {}                            # Managed HTTPRoute
    scheduler: {}                        # llm-d inference scheduler
  baseRefs:                              # Optional: inherit from config templates
    - name: my-org-defaults
```

**Key CRD fields**: `model` (URI, LoRA, criticality), `replicas`, `parallelism` (TP, DP, PP, EP), `template` (decode pods), `worker` (multi-node), `prefill` (disaggregated), `router` (gateway, HTTPRoute, scheduler), `baseRefs` (config inheritance).

---

## 3. Decision: Wrap, Don't Build

### What SkyPilot Should NOT Build

SkyPilot should **not** reimplement any of these -- the ecosystem handles them:

- Load balancing / request proxy (GIE + Envoy)
- KV cache-aware routing (llm-d inference scheduler)
- Prefill-decode disaggregation (llm-d + KServe)
- LLM autoscaling (KEDA + vLLM metrics)
- Pod lifecycle management (KServe controller)
- KV cache distribution (LMCache)
- Inference engine (vLLM)

### What SkyPilot SHOULD Build (The Value-Add)

| SkyPilot Value-Add | The Gap It Fills |
|---|---|
| **1. Simplified UX** | KServe requires container images, GPU resource limits, parallelism settings. No "just a model" experience. SkyPilot: `model: meta-llama/Llama-3.1-70B-Instruct` and we figure out GPU type, TP, engine args, autoscaling. |
| **2. Multi-cluster orchestration** | KServe is single-cluster. SkyPilot deploys across GKE, EKS, on-prem; shifts traffic between them; unified status. |
| **3. GPU availability + cost optimization** | KServe has no GPU pricing awareness. SkyPilot picks cheapest cluster/GPU combo, supports spot with recovery, auto-fallback (H100 unavailable → A100 with adjusted TP). |
| **4. Unified CLI/SDK + Dashboard** | KServe uses kubectl. SkyPilot provides `sky serve up/status/logs/update/down` + dashboard with TTFT, TPOT, KV cache util, cost tracking. |
| **5. Day-2 operations** | Rolling updates with canary, cost reports, alerting integration, auto-remediation. |
| **6. Prerequisite management** | Auto-install KServe + GIE + KEDA on cluster if not present. |

---

## 4. Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                      SkyPilot Layer                          │
│                                                              │
│  sky serve up                                                │
│    │                                                         │
│    ├─► YAML Parser (SkyPilot serve spec)                     │
│    │                                                         │
│    ├─► Model Config Resolver                                 │
│    │   model ID → GPU type, count, TP, engine args           │
│    │   (uses HF model card metadata + SkyPilot catalog)      │
│    │                                                         │
│    ├─► Cluster Selector                                      │
│    │   picks best K8s cluster (GPU availability + cost)      │
│    │                                                         │
│    ├─► K8s Resource Generator                                │
│    │   SkyPilot spec → LLMInferenceService YAML              │
│    │                 + KEDA ScaledObject (if autoscaling)     │
│    │                 + HF token Secret                        │
│    │                                                         │
│    └─► kubectl apply to target cluster                       │
│                                                              │
│  sky serve status / logs / update / down                     │
│    └─► reads LLMInferenceService status + pod status         │
│        + Prometheus metrics (for dashboard)                  │
│                                                              │
│  SkyPilot Dashboard                                          │
│    └─► per-service view: TTFT, TPOT, KV util, GPU util,     │
│        replica count, cost                                   │
└─────────────────────────────┬────────────────────────────────┘
                              │ kubectl apply / helm
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                  Kubernetes Cluster                           │
│                                                              │
│  KServe ─► LLMInferenceService controller                    │
│            creates Deployments, Services, LeaderWorkerSets    │
│                                                              │
│  GIE ────► Envoy Gateway + EPP + Body-Based Router           │
│            model-aware routing, InferencePool/InferenceModel  │
│                                                              │
│  llm-d ──► Inference scheduler (KV cache-aware routing)      │
│            PD disaggregation, hierarchical KV cache (LMCache) │
│                                                              │
│  vLLM ───► Model inference pods (/metrics, OpenAI API)       │
│                                                              │
│  KEDA ───► Autoscaling on vLLM metrics                       │
│  Prometheus ► Metrics collection                             │
└──────────────────────────────────────────────────────────────┘
```

### Request Flow

1. `sky serve up llama.yaml` with `model: meta-llama/Llama-3.1-70B-Instruct`
2. **Model Config Resolver**: 70B params → needs 4x A100-80GB, TP=4, vLLM engine args set
3. **Cluster Selector**: checks GPU availability across registered K8s clusters, picks cheapest
4. **K8s Resource Generator**: generates `LLMInferenceService` + KEDA ScaledObject + Secrets
5. `kubectl apply` to target cluster
6. **KServe controller** takes over: creates Deployments, Services, Gateway, llm-d scheduler
7. **Traffic flows**: Client → Envoy → EPP (llm-d) → vLLM pod with best KV cache hit
8. **Dashboard**: SkyPilot scrapes Prometheus, displays unified metrics

### Responsibility Split

| Concern | Owner |
|---------|-------|
| Which cluster to deploy on | **SkyPilot** |
| GPU type/cost selection | **SkyPilot** |
| Model → config resolution (GPU, TP, engine args) | **SkyPilot** |
| Generate K8s resources | **SkyPilot** |
| CLI / SDK / Dashboard | **SkyPilot** |
| Multi-cluster coordination | **SkyPilot** |
| Pod lifecycle (create/scale/update/delete) | **KServe** |
| Request routing (KV cache, PD disaggregation) | **llm-d** (via KServe) |
| Gateway / ingress | **GIE** (via KServe) |
| Autoscaling decisions | **KEDA** (via KServe) |
| Inference execution | **vLLM** |

---

## 5. User-Facing Spec

### Tier 1: Minimal (just a model)
```yaml
model: meta-llama/Llama-3.1-70B-Instruct
```

SkyPilot auto-resolves: GPU type + count, TP, engine, engine args, replicas, autoscaling defaults, KV cache config. User gets back an OpenAI-compatible endpoint.

### Tier 2: With overrides
```yaml
model: meta-llama/Llama-3.1-70B-Instruct
resources:
  accelerators: H100:2
service:
  replicas: 2
  max_replicas: 8
```

### Tier 3: Full control
```yaml
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
    mode: kv_cache_aware             # round_robin | kv_cache_aware
    disaggregated: true              # Enable PD disaggregation

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
```

### Tier 4: Raw KServe passthrough (escape hatch)
```yaml
kserve:
  apiVersion: serving.kserve.io/v1alpha1
  kind: LLMInferenceService
  spec:
    # ... raw LLMInferenceService spec, applied as-is
```

---

## 6. Implementation Plan

### Phase 1: Core Wrapping (MVP)

**Goal**: `sky serve up/status/down/logs` works for LLM models on a K8s cluster with KServe pre-installed.

#### 1.1 SkyPilot Serve Spec Parser

**New file**: `sky/serve/serve_spec_v2.py`

Parse the new YAML format (Tiers 1-4 above). Validate fields. Convert the SkyPilot spec into an intermediate representation.

```python
@dataclasses.dataclass
class SkyServeSpec:
    model: Optional[str]               # HF model ID
    engine: str = 'vllm'               # inference engine
    engine_args: Dict[str, Any]        # engine-specific args
    resources: ResourceSpec            # GPU type, count
    service: ServiceConfig             # replicas, autoscaling, routing
    kserve_raw: Optional[Dict]         # Tier 4 passthrough
```

#### 1.2 Model Config Resolver

**New file**: `sky/serve/model_registry.py`

Maps model IDs to resource requirements. Sources:
- Built-in table for popular models (Llama, Mistral, Qwen, Gemma, etc.)
- HuggingFace model card metadata (parameter count, architecture) as fallback
- User overrides always take precedence

```python
def resolve_model_config(model_id: str) -> ModelConfig:
    """Returns GPU type, count, TP, engine args for a model."""
    # 1. Check built-in registry
    # 2. Fetch HF model card (param count, architecture)
    # 3. Compute: params * bytes_per_param / GPU_memory → num_gpus
    # 4. Set TP = num_gpus, pick engine args
    return ModelConfig(
        gpu_type='A100-80GB', num_gpus=4,
        tensor_parallel=4, engine_args={...}
    )
```

#### 1.3 K8s Resource Generator

**New file**: `sky/serve/kserve_generator.py`

Translates `SkyServeSpec` → K8s resources:

1. `LLMInferenceService` YAML (core resource)
2. `Secret` for HuggingFace token (if needed)
3. `KEDA ScaledObject` (if autoscaling enabled, Phase 2)

```python
def generate_kserve_resources(spec: SkyServeSpec,
                               cluster_context: str) -> List[Dict]:
    """Generate K8s resource manifests from SkyPilot spec."""
    resources = []

    # LLMInferenceService
    llmisvc = {
        'apiVersion': 'serving.kserve.io/v1alpha1',
        'kind': 'LLMInferenceService',
        'metadata': {'name': spec.service.name, ...},
        'spec': {
            'model': {'uri': f'hf://{spec.model}', ...},
            'replicas': spec.service.replicas,
            'parallelism': {'tensorParallelSize': spec.resources.tp},
            'template': {  # pod spec with GPU resources },
            'router': {'gateway': {}, 'route': {}, 'scheduler': {}},
        }
    }
    if spec.service.routing.disaggregated:
        llmisvc['spec']['prefill'] = { ... }

    resources.append(llmisvc)
    return resources
```

#### 1.4 Prerequisite Checker

**New file**: `sky/serve/kserve_prereqs.py`

Before deploying, verify the target cluster has required components:
- KServe v0.15+ installed (check for CRD `llminferenceservices.serving.kserve.io`)
- Gateway API + GIE (check for CRD `inferencepools.inference.networking.x-k8s.io`)
- Envoy Gateway or Istio (check for GatewayClass resource)
- GPU operator (check for `nvidia.com/gpu` resource on nodes)

Provide clear error messages with install instructions if missing.

#### 1.5 CLI Commands

**Modify**: `sky/client/cli/command.py` (serve subcommands)

| Command | What it does |
|---------|-------------|
| `sky serve up <yaml>` | Parse spec → resolve model → select cluster → generate resources → kubectl apply |
| `sky serve status [name]` | Read LLMInferenceService status + pod status from cluster |
| `sky serve down <name>` | Delete LLMInferenceService + associated resources |
| `sky serve logs <name> [--replica N]` | Stream pod logs (kubectl logs) |
| `sky serve endpoint <name>` | Print the service URL (from LLMInferenceService status) |

#### 1.6 SDK Methods

**Modify**: `sky/serve/client/sdk.py`

Update `up()`, `down()`, `status()` to use the new KServe backend. Keep the same function signatures for backward compat where possible.

#### 1.7 Serve State Tracking

**Modify**: `sky/serve/serve_state.py`

Track deployed services in SkyPilot's database:
- Service name, cluster, namespace
- LLMInferenceService resource name
- Creation time, status
- Model, resource config

This lets `sky serve status` work without querying every cluster.

---

### Phase 2: Auto-Install + KServe Native + Dashboard + Cross-Cluster Metrics

**Status:** Implemented & Validated (2026-02-28)

**Goal**: Prerequisite auto-installation, KServe LLMInferenceService native mode, PD disaggregation, KEDA autoscaling, dashboard integration, cross-cluster vLLM metrics.

#### 2.1 Prerequisite Auto-Installer

**New file**: `sky/serve/kserve_installer.py`

Auto-installs all components needed for KServe LLMInferenceService mode. Runs transparently during `sky serve up` (or explicitly via `sky serve install`). Idempotent -- skips already-installed components.

**Components installed** (4-phase ordering for dependency resolution):
| Phase | Component | Version | Install Method |
|-------|-----------|---------|----------------|
| 1 (parallel) | cert-manager | v1.16.3 | kubectl apply |
| 1 (parallel) | KEDA | v2.16.1 | Helm (fallback: kubectl apply) |
| 1 (parallel) | Prometheus | latest | Helm (prometheus-community/prometheus) |
| 1 (parallel) | LeaderWorkerSet | v0.7.0 | kubectl apply |
| 2 | KServe | v0.15.1 | kubectl apply --server-side |
| 3 (parallel) | Gateway API CRDs | v1.2.0 | kubectl apply |
| 3 (parallel) | Gateway Inference Extension | v1.3.0 | kubectl apply |
| 3 (parallel) | Envoy Gateway | v1.6.3 | Helm |
| 4 | LLMInferenceService CRD + controller | v0.16.0 | Helm template + kubectl apply |

**Key implementation details:**
- KServe manifest uses `--server-side --force-conflicts` (annotations exceed 262KB limit)
- KEDA has Helm-first with kubectl fallback (Helm not available in all environments)
- All functions accept `context: Optional[str]` for multi-cluster targeting
- All prerequisite checks in `kserve_prereqs.py` also accept `context` parameter

#### 2.2 KServe LLMInferenceService Mode

The generator (`kserve_generator.py`) now produces native KServe `LLMInferenceService` resources instead of plain Deployments when prerequisites are available:

- **Template format**: LLMInferenceService uses PodSpec (not PodTemplateSpec) -- `template.containers` directly, no `template.metadata` or `template.spec` wrapper
- **Main container**: Overrides the controller-injected `main` container (llm-d image) with GPU resources and vLLM args
- **Model download**: Uses KServe's `storage-initializer` init container with `hf://` URI prefix. ConfigMap `inferenceservice-config` in `kserve` namespace controls memory limits (increased to 8Gi for LLM models)
- **Prometheus annotations**: Applied post-deploy via `kubectl patch` on the Deployment (LLMInferenceService PodSpec doesn't support pod annotations)

#### 2.3 PD Disaggregation

Deployed and validated with separate prefill and decode pools:

- **Decode pod**: 2 containers -- `main` (vLLM on port 8001) + `llm-d-routing-sidecar` (port 8000, proxies to 8001)
- **Prefill pod**: 1 container -- `main` (vLLM on port 8000, no sidecar)
- Generator automatically handles port assignment based on `spec.service.routing.disaggregated`

#### 2.4 KEDA Autoscaling

KEDA ScaledObjects now target the underlying `apps/v1.Deployment` (not LLMInferenceService, which doesn't expose `/scale` subresource). Prometheus queries use `model_name` label (matching vLLM's actual metric labels).

#### 2.5 Cross-Cluster vLLM Metrics

Added `vllm:.*` match pattern to the existing Prometheus federation mechanism in `sky/metrics/utils.py`:
- API server `/gpu-metrics` endpoint now federates vLLM metrics alongside DCGM GPU metrics
- All metrics get `cluster="<context-name>"` label from the federation layer
- GKE Prometheus scrapes the federated endpoint; Grafana queries GKE Prometheus

#### 2.6 Dashboard Integration

- **Services section**: Added to SkyPilot dashboard with Grafana integration
- **vLLM Serving Dashboard**: Updated with `cluster` template variable for multi-cluster filtering
- All Grafana panel queries include `cluster=~"$cluster"` alongside `model_name` filter

#### 2.7 CLI Enhancements

- `--context` flag on all subcommands for multi-cluster targeting
- `--no-auto-install` flag on `up` to skip prerequisite installation
- New `install` subcommand for explicit prerequisite installation

---

### Phase 3: Multi-Cluster + Advanced Features

**Goal**: Cross-cluster deployment, spot support, canary workflows.

#### 3.1 Multi-Cluster Deployment

- Deploy same model across N clusters
- Per-cluster replica counts
- Global traffic routing (DNS-based or global LB)
- Unified `sky serve status` shows all clusters

#### 3.2 Spot / Preemptible Support

- Use spot/preemptible node pools where available
- On preemption: KServe handles pod rescheduling natively on the same cluster
- Cross-cluster failover: if a cluster loses capacity, SkyPilot scales up elsewhere

#### 3.3 Canary Deployments

- Deploy v2 alongside v1
- Shift traffic incrementally (via GIE InferenceModel traffic weights)
- Monitor metrics, auto-promote or rollback

#### 3.4 KV Cache-Aware Routing via llm-d Scheduler

The `router.scheduler` field in LLMInferenceService enables the llm-d inference scheduler, but requires the `InferenceModel` CRD (`inference.networking.x-k8s.io/v1alpha2`) which is not yet available in any released Gateway API Inference Extension version (checked v1.3.0 and v1.3.1). This is a gap between KServe v0.16.0 controller expectations and upstream CRD availability. Will be unblocked when the CRD is released.

---

### File Change Summary

| File | Phase | Action | Description |
|------|-------|--------|-------------|
| `sky/serve/serve_spec_v2.py` | 1 | **New** | 4-tier YAML spec parser. Handles `model: "id"` and `model: {id: "..."}` formats. Autoscaling shorthand (`target_metric`/`target_value`). |
| `sky/serve/model_registry.py` | 1 | **New** | Model ID → resource requirements mapping (~15 popular models) |
| `sky/serve/kserve_generator.py` | 1+2 | **New** | SkyPilot spec → LLMInferenceService + KEDA ScaledObject + Secrets. KServe native mode + direct Deployment fallback. PD disagg port handling. |
| `sky/serve/kserve_prereqs.py` | 1+2 | **New** | Prerequisite checker with `context` parameter for multi-cluster |
| `sky/serve/kserve_installer.py` | 2 | **New** | Auto-install module: cert-manager, KServe, LLMInferenceService, KEDA, Gateway API, Envoy Gateway, LWS, Prometheus. 4-phase dependency ordering. |
| `sky/serve/serve_v2.py` | 1+2 | **New** | Main orchestration with `context` param on all functions. Post-deploy Prometheus annotation patching for KServe mode. |
| `sky/serve/skyserve_v2_cli.py` | 1+2 | **New** | CLI: `--context`, `--no-auto-install` flags; `install` subcommand |
| `sky/metrics/utils.py` | 2 | **Modify** | Added `vllm:.*` to federation match patterns for cross-cluster metrics |
| `charts/skypilot/manifests/vllm-serving-dashboard.json` | 2 | **Modify** | Added `cluster` template variable; all queries filter by `cluster=~"$cluster"` |
| `sky/dashboard/src/app/services/` | 2 | **New** | Dashboard services section with Grafana integration |

---

## 7. Phase 1 Implementation Results

Phase 1 has been fully implemented and validated on a live CoreWeave K8s cluster (4 nodes, 2x 8xH100 NVLink 80GB + 2x CPU-only).

### Infrastructure Installed on Cluster

| Component | Version | Namespace | Purpose |
|-----------|---------|-----------|---------|
| cert-manager | v1.16.3 | cert-manager | TLS certificate management (KServe dependency) |
| KServe | v0.15.1 | kserve | LLM serving control plane (InferenceService CRDs) |
| KEDA | latest | keda | Kubernetes Event-Driven Autoscaling for vLLM metrics |

Prometheus was already present in the `skypilot` namespace with annotation-based pod auto-discovery.

### Code Implemented

| File | Lines | Purpose |
|------|-------|---------|
| `sky/serve/model_registry.py` | ~260 | Maps HuggingFace model IDs to GPU requirements (type, count, TP, engine args). Built-in registry for ~15 popular models. Auto-infers param count from model name for unknown models. |
| `sky/serve/serve_spec_v2.py` | ~200 | 4-tier YAML spec parser. Dataclasses for `SkyServeSpec`, `ServiceConfig`, `ResourceConfig`, `RoutingConfig`, `AutoscalingConfig`. Validation. |
| `sky/serve/kserve_generator.py` | ~530 | Generates K8s resources from spec. Two modes: `generate_resources()` for KServe `LLMInferenceService`, `generate_direct_deployment()` for standard Deployment+Service fallback. Also generates KEDA ScaledObjects, HF token Secrets, Prometheus annotations. Auto-discovers GPU class labels from cluster. |
| `sky/serve/kserve_prereqs.py` | ~250 | Checks cluster prerequisites: kubectl, cluster connection, GPU nodes, cert-manager, KServe CRDs, Gateway API, KEDA, Prometheus. Clear error messages with install hints. |
| `sky/serve/serve_v2.py` | ~560 | Main orchestration: `up()` (deploy), `status()` (read K8s + Prometheus metrics), `down()` (teardown), `logs()` (aggregate pod logs), `endpoint()` (get service URL), `port_forward()`, `wait_for_ready()`. Prometheus integration for live KV cache usage, request counts, token throughput. |
| `sky/serve/skyserve_v2_cli.py` | ~120 | CLI entry point with argparse subcommands: up, status, down, logs, endpoint, prereqs, generate. |

### What Was Tested End-to-End

All tests were run against a real Qwen 2.5 7B Instruct model deployed on 1x H100 NVLink 80GB.

#### Deployment
- `serve_v2.up()` correctly parses spec, resolves model config (1x H100, TP=1), generates Deployment+Service YAML, applies via kubectl
- Auto-discovers GPU node label (`H100_NVLINK_80GB`) from cluster via `_discover_gpu_class_label()`
- Model loaded and ready in ~60s (vLLM image cached, model weights downloaded from HuggingFace)

#### Querying (OpenAI-compatible API)
| Endpoint | Result |
|----------|--------|
| `/v1/chat/completions` | Correct responses ("Paris", "Four", math problems) |
| `/v1/completions` | Text completion working |
| `/v1/chat/completions` (streaming) | SSE streaming with proper `data:` chunks |
| `/v1/models` | Returns correct model list with `max_model_len=8192` |
| `/health` | 200 OK (used by readiness/liveness probes) |
| `/metrics` | Full vLLM Prometheus metrics exposed |

#### Observability
- Prometheus annotation-based auto-discovery working (`prometheus.io/scrape: "true"`)
- Prometheus scraping vLLM metrics every 60s
- `print_status()` shows live metrics from Prometheus:
  ```
  SkyServe v2 Services (skyserve-v2)
  ======================================================================
    Service:  qwen2-5-7b-instruct
    Type:     Deployment
    Status:   READY
    Replicas: 1/1
    Pods:
      - qwen2-5-7b-instruct-...: Running (ready: 1/1, node: g73af84, GPUs: 1)
    Metrics:
      KV Cache Usage:     0.0%
      Requests Running:   0
      Requests Waiting:   0
      Total Requests:     5
      Prompt Tokens:      185
      Generation Tokens:  46
  ```
- Key vLLM metrics confirmed in Prometheus: `vllm:kv_cache_usage_perc`, `vllm:num_requests_running`, `vllm:num_requests_waiting`, `vllm:request_success_total`, `vllm:prompt_tokens_total`, `vllm:generation_tokens_total`

#### Autoscaling (KEDA)
- KEDA ScaledObject created with Prometheus triggers (queue depth + KV cache utilization)
- HPA created automatically by KEDA, correctly reading vLLM metrics from Prometheus
- **Scale-up verified**: Deployment scaled from 1 to 2 replicas (and 4) when metric exceeded threshold
- New replicas automatically distributed across GPU nodes (g73af84 and g83a19c)
- **Scale-down verified**: Replicas properly terminated when load dropped
- Production config: dual triggers on `vllm:num_requests_waiting > 5` and `vllm:kv_cache_usage_perc > 0.8`

#### Teardown
- `serve_v2.down()` cleanly removes all labeled resources (Deployment, Service, Secret, ScaledObject)
- Label-based cleanup (`app.kubernetes.io/managed-by=skypilot-serve`) ensures no orphaned resources

#### E2E Test Suite
12 automated tests in `test_skyserve_v2_e2e.py`, all passing (128s total runtime):
1. Prerequisites check
2. Spec parsing
3. Deploy model
4. Wait for ready
5. Chat completion
6. Text completion
7. Models endpoint
8. Status
9. Status with metrics
10. Endpoint retrieval
11. Logs
12. Teardown

### Key Design Decisions Made During Implementation

1. **Direct deployment fallback**: The `LLMInferenceService` CRD (v1alpha1) is not yet available in all KServe releases. The generator has a `generate_direct_deployment()` mode that produces standard K8s Deployment+Service, enabling immediate use without waiting for CRD availability.

2. **GPU label auto-discovery**: The model registry uses generic GPU names ("H100") but cluster labels are more specific ("H100_NVLINK_80GB"). `_discover_gpu_class_label()` queries the cluster at deploy time and does prefix matching.

3. **YAML anchor avoidance**: `kubectl apply` can have issues with YAML anchors (`&id001`/`*id001`). The generator uses a `NoAliasDumper` to produce flat YAML.

4. **Dual HF token env vars**: vLLM/transformers check both `HF_TOKEN` and `HUGGING_FACE_HUB_TOKEN`. The generator sets both from the same Secret.

5. **Prometheus integration via annotations**: Rather than requiring ServiceMonitor CRDs (which need the Prometheus Operator), the deployment adds `prometheus.io/scrape` annotations that work with standard Prometheus configuration.

6. **Standalone module execution**: The modules can run independently of the `sky` package by using try/except import fallbacks and `sys.modules` lookups, enabling standalone testing without the full SkyPilot dependency tree.

---

## 8. Phase 2 Implementation Results

Phase 2 has been implemented and validated on a live CoreWeave K8s cluster (1x 8xH200 GPU node).

### Additional Infrastructure Installed on Cluster

| Component | Version | Namespace | Purpose |
|-----------|---------|-----------|---------|
| KServe LLMInferenceService CRD + controller | v0.16.0 | kserve | Native LLM serving CRD with PD disaggregation support |
| Gateway API CRDs | v1.2.0 | (cluster-scoped) | Gateway, HTTPRoute, GatewayClass CRDs |
| Gateway Inference Extension | v1.3.0 | (cluster-scoped) | InferencePool, InferencePoolImport CRDs |
| Envoy Gateway | v1.6.3 | envoy-gateway-system | Gateway API implementation (GatewayClass controller) |
| LeaderWorkerSet | v0.7.0 | lws-system | Multi-node inference support |

### What Was Tested End-to-End

All tests run against `Qwen/Qwen2.5-3B-Instruct` deployed via KServe LLMInferenceService on CoreWeave H200.

#### Auto-Install Prerequisites
- `kserve_installer.ensure_all_prerequisites(context='coreweave-dev')` successfully installed all components
- Idempotent: second run detects all components already installed, skips
- KServe install uses `--server-side --force-conflicts` to handle >262KB annotation limit
- KEDA falls back to kubectl when Helm is not available in the environment

#### KServe LLMInferenceService Deployment
- LLMInferenceService created with `hf://` model URI → storage-initializer downloads model weights
- Controller injects `main` container from `ghcr.io/llm-d/llm-d-dev:v0.2.2` image
- Generator overrides `main` container with GPU resources and vLLM args
- `inferenceservice-config` ConfigMap patched to 8Gi memory for storage-initializer (default 1Gi OOMs for LLM models)
- LLMInferenceService status shows `Ready: True` with all conditions met

#### PD Disaggregation
- Two separate Deployments created: `*-kserve` (decode) and `*-kserve-prefill` (prefill)
- Decode pod: 2 containers (`main` vLLM on port 8001 + `llm-d-routing-sidecar` on port 8000)
- Prefill pod: 1 container (`main` vLLM on port 8000, no sidecar)
- Both pods healthy, LLMInferenceService shows `PrefillWorkloadReady: True` and `MainWorkloadReady: True`
- OpenAI-compatible API accessible through the routing sidecar (port 8000)

#### KEDA Autoscaling
- ScaledObject targets `apps/v1.Deployment` (LLMInferenceService doesn't expose `/scale` subresource)
- Prometheus trigger on `vllm:gpu_cache_usage_perc{model_name="Qwen/Qwen2.5-3B-Instruct"}`
- HPA created: min=1, max=2, threshold=0.8 (80% KV cache utilization)
- ScaledObject status: `Ready: True`, `Active: False` (no load → metric at 0)

#### Cross-Cluster vLLM Metrics
- vLLM metrics federated from CoreWeave Prometheus → GKE API server `/gpu-metrics` → GKE Prometheus → Grafana
- All metrics carry `cluster="coreweave-dev"` label
- Verified in GKE Prometheus: `vllm:gpu_cache_usage_perc{model_name="Qwen/Qwen2.5-3B-Instruct", cluster="coreweave-dev"}`
- Grafana dashboard shows vLLM metrics with cluster filter

#### KV Cache-Aware Routing
- **Blocked**: `router.scheduler` in LLMInferenceService requires `InferenceModel` CRD (`inference.networking.x-k8s.io/v1alpha2`) which is not available in any released Gateway API Inference Extension version (v1.3.0 or v1.3.1)
- PD disaggregation works without the scheduler; the llm-d routing sidecar handles proxying

### Key Design Decisions Made During Phase 2

1. **Port assignment for PD disaggregation**: The llm-d-routing-sidecar (injected by controller on decode pods) occupies port 8000 and proxies to vLLM on port 8001. Prefill pods don't get the sidecar, so they use port 8000 directly. The generator handles this automatically based on `spec.service.routing.disaggregated`.

2. **KEDA targets Deployment, not LLMInferenceService**: The LLMInferenceService CRD doesn't expose the `/scale` subresource needed by KEDA. The ScaledObject targets the underlying `apps/v1.Deployment` created by the controller (named `{service_name}-kserve`).

3. **Prometheus annotations via post-deploy patch**: LLMInferenceService's `template` field is a PodSpec (not PodTemplateSpec), so pod-level annotations can't be set in the spec. Instead, `serve_v2.up()` patches the Deployment after creation to add `prometheus.io/scrape` annotations.

4. **Federation match pattern**: A single `{__name__=~"vllm:.*"}` pattern captures all vLLM metrics (latency, throughput, TTFT, KV cache, queue depth). No new API endpoint needed -- reuses the existing `/gpu-metrics` federation mechanism.

5. **Helm fallback for environments without Helm**: KEDA installation tries Helm first, falls back to `kubectl apply` with the release manifest URL. The manifest URL format uses `keda-{version}.yaml` (without `v` prefix).

---

## 9. Open Questions

1. ~~**Prerequisite installation**: Should `sky serve up` auto-install KServe + GIE + KEDA if not present?~~ **Resolved (Phase 2)**: Yes. `sky serve up` auto-installs all prerequisites by default. Users can opt out with `--no-auto-install`. Explicit installation available via `sky serve install`.

2. **Scope**: Current SkyServe supports any HTTP service. Should v2 be LLM-only (cleaner, simpler) or maintain generality (use KServe's `InferenceService` for non-LLM, `LLMInferenceService` for LLM)?

3. **Backward compatibility**: Should existing SkyServe YAMLs (with `service.readiness_probe`, `run:`, `setup:`) continue to work? Options:
   - (a) Clean break: new spec only, old SkyServe deprecated
   - (b) Detect old format and run via legacy code path
   - (c) Translate old format to new (where possible)

4. **Namespace management**: Should SkyPilot create a dedicated namespace per service, or deploy into a user-specified namespace? Currently uses a shared `skyserve-v2` namespace with label-based resource isolation.

5. **LLMInferenceService is v1alpha1**: It could change. How tightly should we couple to the current API? Should we abstract behind our own spec and regenerate if the CRD changes?

6. **InferenceModel CRD gap**: KServe v0.16.0 controller expects `InferenceModel` CRD at `inference.networking.x-k8s.io/v1alpha2` for `router.scheduler` support, but this CRD is not available in any released Gateway API Inference Extension version (v1.3.0/v1.3.1). KV cache-aware routing via the scheduler is blocked until this CRD is released upstream.

---

## 10. Sources

- [KServe LLMInferenceService Overview](https://kserve.github.io/website/docs/model-serving/generative-inference/llmisvc/llmisvc-overview) -- CRD docs, architecture, YAML examples
- [KServe CRD API Reference](https://kserve.github.io/website/docs/reference/crd-api) -- Full field definitions
- [KServe v0.15 Release Blog](https://kserve.github.io/website/blog/kserve-0.15-release) -- KEDA integration, multi-node, LMCache
- [CNCF: KServe v0.15 Announcement](https://www.cncf.io/blog/2025/06/18/announcing-kserve-v0-15-advancing-generative-ai-model-serving/)
- [KServe KV Cache Offloading Docs](https://kserve.github.io/website/docs/model-serving/generative-inference/kvcache-offloading) -- LMCache integration
- [llm-d GitHub](https://github.com/llm-d/llm-d) -- v0.5.0, Feb 2026. Architecture, components, hardware support
- [llm-d KV Cache Wins Blog](https://llm-d.ai/blog/kvcache-wins-you-can-see) -- 57x TTFT improvement benchmarks
- [KV Cache Aware Routing with llm-d (Red Hat)](https://developers.redhat.com/articles/2025/10/07/master-kv-cache-aware-routing-llm-d-efficient-ai-inference)
- [Cloud-Native LLM Inference Stack](https://jimmysong.io/blog/cloud-native-llm-inference-stack/) -- KServe + vLLM + llm-d + GIE architecture overview
- [Gateway API Inference Extension](https://gateway-api-inference-extension.sigs.k8s.io/) -- InferencePool v1 (stable), InferenceModel, EPP
- [Introducing Gateway API Inference Extension (Kubernetes Blog)](https://kubernetes.io/blog/2025/06/05/introducing-gateway-api-inference-extension/)
- [Deep Dive into Gateway API Inference Extension (CNCF)](https://www.cncf.io/blog/2025/04/21/deep-dive-into-the-gateway-api-inference-extension/)
- [Istio Inference Extension Support](https://istio.io/latest/blog/2025/inference-extension-support/) -- Istio 1.27+ support

### Appendix: Current SkyServe Architecture (for reference)

```
                    SkyPilot API Server
                           │
                     sky serve up
                           │
                    ┌──────▼──────┐
                    │ Controller  │ (dedicated VM)
                    │  VM         │
                    │             │
                    │ ┌─────────┐ │         ┌──────────┐
User ──────────────►│ │   LB    │ │────────►│ Replica 1│
    request         │ │ (Python │ │         │ (vLLM)   │
                    │ │  proxy) │ │────────►│          │
                    │ └─────────┘ │         └──────────┘
                    │             │
                    │ ┌─────────┐ │         ┌──────────┐
                    │ │Autoscale│ │         │ Replica 2│
                    │ │(QPS     │ │────────►│ (vLLM)   │
                    │ │ only)   │ │         │          │
                    │ └─────────┘ │         └──────────┘
                    │             │
                    │ ┌─────────┐ │
                    │ │Replica  │ │
                    │ │Manager  │ │
                    │ └─────────┘ │
                    └─────────────┘

Problems:
  1. Python LB is the bottleneck (all traffic proxied through it)
  2. Controller VM is SPOF and cost center
  3. Only QPS-based autoscaling
  4. No AI-specific routing intelligence
  5. No observability beyond basic status
```
