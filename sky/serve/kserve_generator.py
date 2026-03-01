"""KServe resource generator for SkyServe v2.

Translates SkyServeSpec into Kubernetes resource manifests
(LLMInferenceService, Secrets, KEDA ScaledObjects).
"""
import copy
import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

try:
    from sky.serve import model_registry
    from sky.serve import serve_spec_v2
except (ImportError, ModuleNotFoundError):
    # Standalone mode -- modules already in sys.modules
    import sys
    model_registry = sys.modules.get('sky.serve.model_registry')
    serve_spec_v2 = sys.modules.get('sky.serve.serve_spec_v2')

logger = logging.getLogger(__name__)

# Default vLLM image
DEFAULT_VLLM_IMAGE = 'vllm/vllm-openai:latest'

# GPU resource name mapping
GPU_K8S_RESOURCE = {
    'H100': 'nvidia.com/gpu',
    'H100_NVLINK_80GB': 'nvidia.com/gpu',
    'H100_SXM': 'nvidia.com/gpu',
    'A100': 'nvidia.com/gpu',
    'A100-80GB': 'nvidia.com/gpu',
    'A100-40GB': 'nvidia.com/gpu',
    'A10G': 'nvidia.com/gpu',
    'L4': 'nvidia.com/gpu',
    'L40': 'nvidia.com/gpu',
    'L40S': 'nvidia.com/gpu',
    'T4': 'nvidia.com/gpu',
    'V100': 'nvidia.com/gpu',
}


def _discover_gpu_class_label(gpu_type: str) -> Optional[str]:
    """Discover the actual gpu.nvidia.com/class label from the cluster.

    The model registry uses short names like 'H100', but cluster labels may be
    more specific like 'H100_NVLINK_80GB'. This function queries the cluster to
    find the actual label value that matches.
    """
    import subprocess
    try:
        result = subprocess.run(
            ['kubectl', 'get', 'nodes', '-o',
             'jsonpath={.items[*].metadata.labels.gpu\\.nvidia\\.com/class}'],
            capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return None
        # Get unique GPU class labels from the cluster
        labels = set(result.stdout.strip().split())
        # Exact match first
        if gpu_type in labels:
            return gpu_type
        # Prefix match: 'H100' matches 'H100_NVLINK_80GB', 'H100_SXM', etc.
        gpu_upper = gpu_type.replace('-', '_').upper()
        for label in labels:
            if label.upper().startswith(gpu_upper):
                logger.info('Mapped GPU type %s to cluster label %s',
                            gpu_type, label)
                return label
        return None
    except Exception:
        return None


def generate_service_name(spec: serve_spec_v2.SkyServeSpec) -> str:
    """Generate a service name from the spec."""
    if spec.service.name:
        return spec.service.name
    if spec.model:
        # Convert model ID to a valid K8s name
        name = spec.model.split('/')[-1].lower()
        # Replace invalid chars
        name = name.replace('_', '-').replace('.', '-')
        # Truncate to 63 chars (K8s name limit)
        return name[:63]
    return 'skyserve-model'


def generate_resources(
    spec: serve_spec_v2.SkyServeSpec,
    namespace: str = 'default',
    hf_token: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], model_registry.ModelConfig]:
    """Generate K8s resource manifests from a SkyServe spec.

    Returns:
        Tuple of (list of K8s resource dicts, resolved ModelConfig)
    """
    # Tier 4: Raw passthrough
    if spec.is_passthrough:
        resource = copy.deepcopy(spec.kserve_raw)
        if 'metadata' not in resource:
            resource['metadata'] = {}
        resource['metadata']['namespace'] = namespace
        return [resource], model_registry.ModelConfig(
            model_id='passthrough',
            gpu_type='unknown',
            num_gpus=0,
            tensor_parallel=1,
            engine='unknown',
        )

    # Resolve model config
    model_config = model_registry.resolve_model_config(
        model_id=spec.model,
        gpu_type=spec.resources.gpu_type,
        num_gpus=spec.resources.num_gpus,
        engine=spec.engine,
        engine_args=spec.engine_args if spec.engine_args else None,
        max_model_len=spec.engine_args.get('max_model_len')
        if spec.engine_args else None,
    )

    service_name = generate_service_name(spec)
    resources = []

    # 1. HuggingFace token secret (if provided)
    if hf_token:
        secret = _generate_hf_secret(service_name, namespace, hf_token)
        resources.append(secret)

    # 2. LLMInferenceService
    llmisvc = _generate_llm_inference_service(
        spec, model_config, service_name, namespace, hf_token is not None)
    resources.append(llmisvc)

    # 3. KEDA ScaledObject (if autoscaling configured)
    if spec.service.autoscaling and spec.service.max_replicas:
        scaled_obj = _generate_keda_scaled_object(
            spec, service_name, namespace)
        resources.append(scaled_obj)

    return resources, model_config


def _generate_hf_secret(service_name: str, namespace: str,
                        hf_token: str) -> Dict[str, Any]:
    """Generate a Kubernetes Secret for HuggingFace token."""
    import base64
    encoded = base64.b64encode(hf_token.encode()).decode()
    return {
        'apiVersion': 'v1',
        'kind': 'Secret',
        'metadata': {
            'name': f'{service_name}-hf-token',
            'namespace': namespace,
            'labels': {
                'app.kubernetes.io/managed-by': 'skypilot-serve',
                'skypilot.co/service': service_name,
            },
        },
        'type': 'Opaque',
        'data': {
            'token': encoded,
        },
    }


def _build_vllm_args(model_config: model_registry.ModelConfig,
                     spec: serve_spec_v2.SkyServeSpec) -> List[str]:
    """Build vLLM command-line arguments."""
    args = [
        '--model', model_config.model_id,
        '--port', '8000',
    ]

    if model_config.tensor_parallel > 1:
        args.extend(['--tensor-parallel-size',
                     str(model_config.tensor_parallel)])

    if model_config.max_model_len:
        args.extend(['--max-model-len', str(model_config.max_model_len)])

    # Add dtype
    args.extend(['--dtype', model_config.default_dtype])

    # Add any extra engine args
    for key, value in model_config.engine_args.items():
        arg_name = f'--{key.replace("_", "-")}'
        if isinstance(value, bool):
            if value:
                args.append(arg_name)
        else:
            args.extend([arg_name, str(value)])

    return args


def _generate_llm_inference_service(
    spec: serve_spec_v2.SkyServeSpec,
    model_config: model_registry.ModelConfig,
    service_name: str,
    namespace: str,
    has_hf_secret: bool,
) -> Dict[str, Any]:
    """Generate LLMInferenceService resource."""
    gpu_resource = GPU_K8S_RESOURCE.get(model_config.gpu_type,
                                        'nvidia.com/gpu')

    # Build extra vLLM args (beyond what the template provides)
    extra_args = []
    if model_config.max_model_len:
        extra_args.extend(['--max-model-len', str(model_config.max_model_len)])
    if model_config.default_dtype != 'bfloat16':
        extra_args.extend(['--dtype', model_config.default_dtype])
    if model_config.tensor_parallel > 1:
        extra_args.extend(
            ['--tensor-parallel-size', str(model_config.tensor_parallel)])
    for k, v in model_config.engine_args.items():
        arg_name = f'--{k.replace("_", "-")}'
        if isinstance(v, bool):
            if v:
                extra_args.append(arg_name)
        else:
            extra_args.extend([arg_name, str(v)])

    # Override the main container with GPU resources + extra args
    main_container = {
        'name': 'main',
        'resources': {
            'requests': {
                gpu_resource: str(model_config.num_gpus),
                'cpu': str(max(4, model_config.num_gpus * 4)),
                'memory': f'{max(16, model_config.num_gpus * 16)}Gi',
            },
            'limits': {
                gpu_resource: str(model_config.num_gpus),
            },
        },
    }
    # When PD disaggregation is enabled, the llm-d-routing-sidecar uses
    # port 8000 and proxies to vLLM on port 8001
    vllm_port = '8001' if spec.service.routing.disaggregated else '8000'
    if extra_args:
        main_container['args'] = [
            '--served-model-name', model_config.model_id,
            '--port', vllm_port,
            '--disable-log-requests',
        ] + extra_args

    # Add HF token env vars if secret exists
    if has_hf_secret:
        hf_env = {
            'valueFrom': {
                'secretKeyRef': {
                    'name': f'{service_name}-hf-token',
                    'key': 'token',
                },
            },
        }
        main_container['env'] = [
            {'name': 'HF_TOKEN', **hf_env},
            {'name': 'HUGGING_FACE_HUB_TOKEN', **hf_env},
        ]

    # Build the LLMInferenceService
    llmisvc = {
        'apiVersion': 'serving.kserve.io/v1alpha1',
        'kind': 'LLMInferenceService',
        'metadata': {
            'name': service_name,
            'namespace': namespace,
            'labels': {
                'app.kubernetes.io/managed-by': 'skypilot-serve',
                'skypilot.co/service': service_name,
            },
        },
        'spec': {
            'model': {
                'uri': f'hf://{model_config.model_id}',
                'name': model_config.model_id,
            },
            'replicas': spec.service.replicas,
            'template': {
                'containers': [main_container],
            },
        },
    }

    # Add parallelism
    if model_config.tensor_parallel > 1:
        llmisvc['spec']['parallelism'] = {
            'tensorParallelSize': model_config.tensor_parallel,
        }

    # Add router config
    if spec.service.routing.mode == 'kv_cache_aware':
        llmisvc['spec']['router'] = {
            'gateway': {},
            'route': {},
            'scheduler': {},  # Enables llm-d inference scheduler
        }

    # Add LoRA adapters
    if spec.service.lora_adapters:
        llmisvc['spec']['model']['lora'] = [
            {'name': a.name, 'uri': a.uri}
            for a in spec.service.lora_adapters
        ]

    # Add prefill for PD disaggregation
    if (spec.service.routing.disaggregated and
            spec.service.prefill is not None):
        prefill_container = copy.deepcopy(main_container)
        # Prefill pod doesn't get routing sidecar, so use port 8000
        if prefill_container.get('args'):
            prefill_args = list(prefill_container['args'])
            for i, arg in enumerate(prefill_args):
                if arg == '--port' and i + 1 < len(prefill_args):
                    prefill_args[i + 1] = '8000'
            prefill_container['args'] = prefill_args
        if spec.service.prefill.accelerators:
            parts = spec.service.prefill.accelerators.split(':')
            gpu_count = int(parts[1]) if len(parts) > 1 else 1
            prefill_container['resources']['requests'][
                gpu_resource] = str(gpu_count)
            prefill_container['resources']['limits'][
                gpu_resource] = str(gpu_count)

        llmisvc['spec']['prefill'] = {
            'replicas': spec.service.prefill.replicas,
            'template': {
                'containers': [prefill_container],
            },
        }

    return llmisvc


def _generate_keda_scaled_object(
    spec: serve_spec_v2.SkyServeSpec,
    service_name: str,
    namespace: str,
) -> Dict[str, Any]:
    """Generate KEDA ScaledObject for autoscaling."""
    triggers = []
    for metric in spec.service.autoscaling.metrics:
        if metric.type == 'kv_cache_utilization':
            triggers.append({
                'type': 'prometheus',
                'metadata': {
                    'serverAddress': ('http://skypilot-prometheus-server'
                                        '.skypilot.svc.cluster.local:80'),
                    'metricName': 'vllm_gpu_cache_usage_perc',
                    'query': (
                        f'avg(vllm:gpu_cache_usage_perc'
                        f'{{model_name="{spec.model}"}})'),
                    'threshold': str(metric.target),
                },
            })
        elif metric.type == 'queue_depth':
            triggers.append({
                'type': 'prometheus',
                'metadata': {
                    'serverAddress': ('http://skypilot-prometheus-server'
                                        '.skypilot.svc.cluster.local:80'),
                    'metricName': 'vllm_num_requests_waiting',
                    'query': (
                        f'sum(vllm:num_requests_waiting'
                        f'{{model_name="{spec.model}"}})'),
                    'threshold': str(metric.target),
                },
            })
        elif metric.type == 'requests_per_second':
            triggers.append({
                'type': 'prometheus',
                'metadata': {
                    'serverAddress': ('http://skypilot-prometheus-server'
                                        '.skypilot.svc.cluster.local:80'),
                    'metricName': 'requests_per_second',
                    'query': (
                        f'sum(rate(vllm:request_success_total'
                        f'{{model_name="{spec.model}"}}[1m]))'),
                    'threshold': str(metric.target),
                },
            })

    return {
        'apiVersion': 'keda.sh/v1alpha1',
        'kind': 'ScaledObject',
        'metadata': {
            'name': f'{service_name}-autoscaler',
            'namespace': namespace,
            'labels': {
                'app.kubernetes.io/managed-by': 'skypilot-serve',
                'skypilot.co/service': service_name,
            },
        },
        'spec': {
            'scaleTargetRef': {
                'apiVersion': 'apps/v1',
                'kind': 'Deployment',
                'name': f'{service_name}-kserve',
            },
            'minReplicaCount': spec.service.replicas,
            'maxReplicaCount': spec.service.max_replicas,
            'cooldownPeriod': spec.service.autoscaling.downscale_delay,
            'triggers': triggers,
        },
    }


def generate_direct_deployment(
    spec: serve_spec_v2.SkyServeSpec,
    namespace: str = 'default',
    hf_token: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], model_registry.ModelConfig]:
    """Generate direct K8s Deployment + Service (without KServe).

    This is a fallback for clusters that don't have KServe installed.
    Generates standard Deployment + Service + optional Ingress resources.
    """
    model_config = model_registry.resolve_model_config(
        model_id=spec.model,
        gpu_type=spec.resources.gpu_type,
        num_gpus=spec.resources.num_gpus,
        engine=spec.engine,
        engine_args=spec.engine_args if spec.engine_args else None,
    )

    service_name = generate_service_name(spec)
    resources = []

    # HF token secret
    if hf_token:
        resources.append(
            _generate_hf_secret(service_name, namespace, hf_token))

    gpu_resource = GPU_K8S_RESOURCE.get(model_config.gpu_type,
                                        'nvidia.com/gpu')
    vllm_args = _build_vllm_args(model_config, spec)

    # Container definition
    container = {
        'name': 'vllm',
        'image': model_config.image,
        'args': vllm_args,
        'ports': [{'containerPort': 8000, 'name': 'http'}],
        'resources': {
            'requests': {
                gpu_resource: str(model_config.num_gpus),
                'cpu': str(max(4, model_config.num_gpus * 4)),
                'memory': f'{max(16, model_config.num_gpus * 16)}Gi',
            },
            'limits': {
                gpu_resource: str(model_config.num_gpus),
            },
        },
        'readinessProbe': {
            'httpGet': {'path': '/health', 'port': 8000},
            'initialDelaySeconds': 60,
            'periodSeconds': 10,
            'timeoutSeconds': 5,
            'failureThreshold': 30,
        },
        'livenessProbe': {
            'httpGet': {'path': '/health', 'port': 8000},
            'initialDelaySeconds': 120,
            'periodSeconds': 30,
            'timeoutSeconds': 5,
        },
        'volumeMounts': [{
            'name': 'shm',
            'mountPath': '/dev/shm',
        }],
    }

    if hf_token:
        hf_env = {
            'valueFrom': {
                'secretKeyRef': {
                    'name': f'{service_name}-hf-token',
                    'key': 'token',
                },
            },
        }
        container['env'] = [
            {'name': 'HF_TOKEN', **hf_env},
            {'name': 'HUGGING_FACE_HUB_TOKEN', **hf_env},
        ]

    labels = {
        'app': service_name,
        'app.kubernetes.io/managed-by': 'skypilot-serve',
        'skypilot.co/service': service_name,
    }

    # Deployment
    deployment = {
        'apiVersion': 'apps/v1',
        'kind': 'Deployment',
        'metadata': {
            'name': service_name,
            'namespace': namespace,
            'labels': labels,
        },
        'spec': {
            'replicas': spec.service.replicas,
            'selector': {'matchLabels': {'app': service_name}},
            'template': {
                'metadata': {
                    'labels': labels,
                    'annotations': {
                        'prometheus.io/scrape': 'true',
                        'prometheus.io/port': '8000',
                        'prometheus.io/path': '/metrics',
                    },
                },
                'spec': {
                    'containers': [container],
                    'volumes': [{
                        'name': 'shm',
                        'emptyDir': {
                            'medium': 'Memory',
                            'sizeLimit': '16Gi',
                        },
                    }],
                },
            },
        },
    }

    # Node selector for GPU type -- discover actual label from cluster
    if model_config.gpu_type:
        gpu_class = _discover_gpu_class_label(model_config.gpu_type)
        if gpu_class is None:
            # Fallback: use the GPU type as-is (uppercase, underscores)
            gpu_class = model_config.gpu_type.replace('-', '_').upper()
        deployment['spec']['template']['spec']['nodeSelector'] = {
            'gpu.nvidia.com/class': gpu_class,
        }

    resources.append(deployment)

    # Service
    svc = {
        'apiVersion': 'v1',
        'kind': 'Service',
        'metadata': {
            'name': service_name,
            'namespace': namespace,
            'labels': labels,
        },
        'spec': {
            'selector': {'app': service_name},
            'ports': [{
                'name': 'http',
                'port': 8000,
                'targetPort': 8000,
                'protocol': 'TCP',
            }],
            'type': 'ClusterIP',
        },
    }
    resources.append(svc)

    return resources, model_config


def resources_to_yaml(resources: List[Dict[str, Any]]) -> str:
    """Convert list of K8s resource dicts to a multi-document YAML string."""
    import yaml

    class NoAliasDumper(yaml.SafeDumper):
        """YAML dumper that avoids anchors/aliases for kubectl compat."""
        def ignore_aliases(self, data):
            return True

    docs = []
    for r in resources:
        docs.append(yaml.dump(r, Dumper=NoAliasDumper,
                              default_flow_style=False))
    return '---\n'.join(docs)
