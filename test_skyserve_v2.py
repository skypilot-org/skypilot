#!/usr/bin/env python3
"""Test script for SkyServe v2 - runs independently of the sky package."""
import importlib.util
import os
import sys

# Prevent sky/__init__.py from loading by pre-populating sys.modules
# with a dummy sky package
class _DummySky:
    pass
sys.modules['sky'] = _DummySky()
sys.modules['sky.serve'] = _DummySky()
sys.modules['sky.utils'] = _DummySky()

base = os.path.join(os.path.dirname(__file__), 'sky', 'serve')

def load_mod(name, filename):
    path = os.path.join(base, filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

# Load modules in dependency order
model_registry = load_mod('sky.serve.model_registry', 'model_registry.py')
serve_spec_v2 = load_mod('sky.serve.serve_spec_v2', 'serve_spec_v2.py')
kserve_generator = load_mod('sky.serve.kserve_generator', 'kserve_generator.py')
kserve_prereqs = load_mod('sky.serve.kserve_prereqs', 'kserve_prereqs.py')
serve_v2 = load_mod('sky.serve.serve_v2', 'serve_v2.py')


def test_prereqs():
    print('=== PREREQUISITE CHECK ===')
    kserve_prereqs.print_prereq_report()


def test_spec_parsing():
    print('\n=== SPEC PARSING (Tier 1: minimal) ===')
    spec = serve_spec_v2.parse_spec_from_dict({
        'model': 'meta-llama/Llama-3.1-8B-Instruct',
    })
    print(f'  Model: {spec.model}')
    print(f'  Engine: {spec.engine}')
    print(f'  Is minimal: {spec.is_minimal}')
    print(f'  Replicas: {spec.service.replicas}')
    assert spec.model == 'meta-llama/Llama-3.1-8B-Instruct'
    assert spec.is_minimal
    print('  PASSED')

    print('\n=== SPEC PARSING (Tier 3: advanced) ===')
    adv = serve_spec_v2.parse_spec_from_dict({
        'model': 'meta-llama/Llama-3.1-70B-Instruct',
        'engine': 'vllm',
        'engine_args': {'max_model_len': 32768},
        'resources': {'accelerators': 'A100:4'},
        'service': {
            'name': 'my-llm',
            'replicas': 2,
            'max_replicas': 10,
            'routing': {'mode': 'kv_cache_aware', 'disaggregated': True},
            'autoscaling': {
                'metrics': [
                    {'type': 'kv_cache_utilization', 'target': 0.7},
                    {'type': 'queue_depth', 'target': 5},
                ],
            },
            'prefill': {'resources': {'accelerators': 'H100:4'}, 'replicas': 2},
        },
    })
    print(f'  Model: {adv.model}')
    print(f'  Accelerators: {adv.resources.accelerators}')
    print(f'  GPU type: {adv.resources.gpu_type}, Num: {adv.resources.num_gpus}')
    print(f'  Routing: {adv.service.routing.mode}')
    print(f'  Disaggregated: {adv.service.routing.disaggregated}')
    print(f'  Autoscaling metrics: {len(adv.service.autoscaling.metrics)}')
    print(f'  Prefill replicas: {adv.service.prefill.replicas}')
    assert adv.service.routing.mode == 'kv_cache_aware'
    assert adv.service.routing.disaggregated
    assert len(adv.service.autoscaling.metrics) == 2
    errors = serve_spec_v2.validate_spec(adv)
    assert not errors, f'Unexpected errors: {errors}'
    print('  PASSED')


def test_model_registry():
    print('\n=== MODEL CONFIG RESOLUTION ===')
    models = [
        ('meta-llama/Llama-3.1-8B-Instruct', 1, 8),
        ('meta-llama/Llama-3.1-70B-Instruct', 4, 70),
        ('meta-llama/Llama-3.1-405B-Instruct', 8, 405),
        ('Qwen/Qwen2.5-72B-Instruct', 4, 72),
        ('mistralai/Mistral-7B-Instruct-v0.3', 1, 7),
    ]
    for model_id, expected_gpus, expected_params in models:
        config = model_registry.resolve_model_config(model_id)
        print(f'  {model_id}: {config.num_gpus}x {config.gpu_type}, '
              f'TP={config.tensor_parallel}, {config.param_count_billions}B, '
              f'~{config.model_size_gb:.0f}GB')
        assert config.num_gpus == expected_gpus, (
            f'{model_id}: expected {expected_gpus} GPUs, got {config.num_gpus}')
        assert config.param_count_billions == expected_params

    # Test unknown model (infer from name)
    config = model_registry.resolve_model_config('some-org/cool-model-13B')
    print(f'  Unknown 13B model: {config.num_gpus}x {config.gpu_type}')
    assert config.param_count_billions == 13

    # Test with user overrides
    config = model_registry.resolve_model_config(
        'meta-llama/Llama-3.1-8B-Instruct',
        gpu_type='A10G', num_gpus=2)
    print(f'  8B with overrides: {config.num_gpus}x {config.gpu_type}')
    assert config.num_gpus == 2
    assert config.gpu_type == 'A10G'
    print('  PASSED')


def test_yaml_generation_direct():
    print('\n=== DIRECT DEPLOYMENT YAML GENERATION ===')
    spec = serve_spec_v2.parse_spec_from_dict({
        'model': 'meta-llama/Llama-3.1-8B-Instruct',
    })
    resources, mc = kserve_generator.generate_direct_deployment(
        spec, namespace='skyserve-v2')
    yaml_str = kserve_generator.resources_to_yaml(resources)

    assert len(resources) == 2  # Deployment + Service
    assert resources[0]['kind'] == 'Deployment'
    assert resources[1]['kind'] == 'Service'

    deploy = resources[0]
    container = deploy['spec']['template']['spec']['containers'][0]
    assert 'nvidia.com/gpu' in container['resources']['limits']
    assert container['resources']['limits']['nvidia.com/gpu'] == '1'
    assert '--model' in container['args']
    assert 'meta-llama/Llama-3.1-8B-Instruct' in container['args']

    print(f'  Resources: {[r["kind"] for r in resources]}')
    print(f'  GPU: {container["resources"]["limits"]["nvidia.com/gpu"]}')
    print(f'  Image: {container["image"]}')
    print(f'  Args: {" ".join(container["args"][:6])}...')
    print(f'  YAML length: {len(yaml_str)} chars')
    print('  PASSED')
    return yaml_str


def test_yaml_generation_kserve():
    print('\n=== KSERVE YAML GENERATION ===')
    spec = serve_spec_v2.parse_spec_from_dict({
        'model': 'meta-llama/Llama-3.1-8B-Instruct',
        'service': {
            'routing': {'mode': 'kv_cache_aware'},
        },
    })
    resources, mc = kserve_generator.generate_resources(
        spec, namespace='skyserve-v2')
    yaml_str = kserve_generator.resources_to_yaml(resources)

    assert len(resources) == 1  # Just LLMInferenceService
    llmisvc = resources[0]
    assert llmisvc['kind'] == 'LLMInferenceService'
    assert llmisvc['spec']['model']['uri'] == 'hf://meta-llama/Llama-3.1-8B-Instruct'
    assert 'router' in llmisvc['spec']  # KV cache aware routing
    assert 'scheduler' in llmisvc['spec']['router']

    print(f'  Kind: {llmisvc["kind"]}')
    print(f'  Model URI: {llmisvc["spec"]["model"]["uri"]}')
    print(f'  Router: {list(llmisvc["spec"]["router"].keys())}')
    print(f'  YAML length: {len(yaml_str)} chars')
    print('  PASSED')
    return yaml_str


def test_yaml_generation_with_autoscaling():
    print('\n=== KSERVE + AUTOSCALING YAML GENERATION ===')
    spec = serve_spec_v2.parse_spec_from_dict({
        'model': 'meta-llama/Llama-3.1-70B-Instruct',
        'resources': {'accelerators': 'H100:4'},
        'service': {
            'name': 'llama-70b',
            'replicas': 2,
            'max_replicas': 8,
            'routing': {'mode': 'kv_cache_aware'},
            'autoscaling': {
                'metrics': [
                    {'type': 'kv_cache_utilization', 'target': 0.7},
                ],
                'downscale_delay': 600,
            },
        },
    })
    resources, mc = kserve_generator.generate_resources(
        spec, namespace='skyserve-v2')

    assert len(resources) == 2  # LLMInferenceService + ScaledObject
    assert resources[0]['kind'] == 'LLMInferenceService'
    assert resources[1]['kind'] == 'ScaledObject'

    scaled = resources[1]
    assert scaled['spec']['minReplicaCount'] == 2
    assert scaled['spec']['maxReplicaCount'] == 8
    assert len(scaled['spec']['triggers']) == 1
    assert 'gpu_cache_usage_perc' in scaled['spec']['triggers'][0]['metadata']['metricName']

    print(f'  Resources: {[r["kind"] for r in resources]}')
    print(f'  Min/Max replicas: {scaled["spec"]["minReplicaCount"]}/{scaled["spec"]["maxReplicaCount"]}')
    print(f'  Triggers: {len(scaled["spec"]["triggers"])}')
    print('  PASSED')


if __name__ == '__main__':
    test_prereqs()
    test_spec_parsing()
    test_model_registry()
    yaml_direct = test_yaml_generation_direct()
    yaml_kserve = test_yaml_generation_kserve()
    test_yaml_generation_with_autoscaling()

    print('\n' + '=' * 50)
    print('ALL TESTS PASSED')
    print('=' * 50)

    # Print the generated YAML for visual inspection
    print('\n--- Direct Deployment YAML (what we will apply) ---')
    print(yaml_direct)
