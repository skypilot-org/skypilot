#!/usr/bin/env python3
"""End-to-end test for SkyServe v2.

Tests the full lifecycle:
1. Prereq check
2. Deploy model (direct mode)
3. Wait for ready
4. Query the model (chat, completion, streaming)
5. Check status with metrics
6. Test logs
7. Tear down

Requires: kubectl access to a K8s cluster with GPU nodes.
"""
import importlib.util
import json
import os
import subprocess
import sys
import time

# ---- Module loading (standalone, avoid importing sky) ----

class _DummySky:
    pass

sys.modules['sky'] = _DummySky()
sys.modules['sky.serve'] = _DummySky()
sys.modules['sky.utils'] = _DummySky()

BASE = os.path.join(os.path.dirname(__file__), 'sky', 'serve')


def _load(name, filename):
    path = os.path.join(BASE, filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


model_registry = _load('sky.serve.model_registry', 'model_registry.py')
serve_spec_v2 = _load('sky.serve.serve_spec_v2', 'serve_spec_v2.py')
kserve_prereqs = _load('sky.serve.kserve_prereqs', 'kserve_prereqs.py')
kserve_generator = _load('sky.serve.kserve_generator', 'kserve_generator.py')
serve_v2 = _load('sky.serve.serve_v2', 'serve_v2.py')

NAMESPACE = 'skyserve-v2-test'
SPEC_PATH = os.path.join(os.path.dirname(__file__),
                         'examples', 'serve', 'qwen-7b.yaml')
passed = 0
failed = 0


def run(desc, fn):
    global passed, failed
    print(f'\n{"=" * 60}')
    print(f'TEST: {desc}')
    print(f'{"=" * 60}')
    try:
        fn()
        passed += 1
        print(f'  PASSED')
    except Exception as e:
        failed += 1
        print(f'  FAILED: {e}')


# ---- Tests ----

def test_prereqs():
    """Check prerequisites."""
    checks = kserve_prereqs.check_all()
    for name, s in checks.items():
        status_str = 'OK' if s.installed else 'MISSING'
        print(f'  {name}: {status_str}')
    assert checks['kubectl'].installed, 'kubectl not found'
    assert checks['cluster'].installed, 'cluster not connected'


def test_spec_parse():
    """Parse the example spec."""
    spec = serve_spec_v2.parse_spec(SPEC_PATH)
    assert spec.model == 'Qwen/Qwen2.5-7B-Instruct'
    assert spec.resources.gpu_type == 'H100'
    assert spec.resources.num_gpus == 1
    assert spec.service.replicas == 1
    assert spec.service.max_replicas == 2
    print(f'  Model: {spec.model}')
    print(f'  GPU: {spec.resources.num_gpus}x {spec.resources.gpu_type}')


def test_deploy():
    """Deploy the model."""
    result = serve_v2.up(SPEC_PATH, namespace=NAMESPACE, force_direct=True)
    assert result['service_name'] == 'qwen2-5-7b-instruct'
    assert result['mode'] == 'direct'
    print(f'  Service: {result["service_name"]}')
    print(f'  Mode: {result["mode"]}')


def test_wait_for_ready():
    """Wait for the service to become ready."""
    ready = serve_v2.wait_for_ready(
        'qwen2-5-7b-instruct', namespace=NAMESPACE, timeout=300)
    assert ready, 'Service did not become ready within timeout'


def test_query_chat():
    """Test chat completion via port-forward."""
    proc = serve_v2.port_forward(
        'qwen2-5-7b-instruct', namespace=NAMESPACE, local_port=8877)
    try:
        time.sleep(3)
        result = subprocess.run(
            ['curl', '-s', 'http://localhost:8877/v1/chat/completions',
             '-H', 'Content-Type: application/json',
             '-d', json.dumps({
                 'model': 'Qwen/Qwen2.5-7B-Instruct',
                 'messages': [{'role': 'user',
                               'content': 'What is 2+2? Answer in one word.'}],
                 'max_tokens': 5,
                 'temperature': 0,
             })],
            capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout)
        content = data['choices'][0]['message']['content']
        print(f'  Response: {content}')
        assert '4' in content or 'four' in content.lower(), \
            f'Unexpected response: {content}'
    finally:
        proc.kill()
        proc.wait()


def test_query_completion():
    """Test text completion."""
    proc = serve_v2.port_forward(
        'qwen2-5-7b-instruct', namespace=NAMESPACE, local_port=8878)
    try:
        time.sleep(3)
        result = subprocess.run(
            ['curl', '-s', 'http://localhost:8878/v1/completions',
             '-H', 'Content-Type: application/json',
             '-d', json.dumps({
                 'model': 'Qwen/Qwen2.5-7B-Instruct',
                 'prompt': 'Hello, my name is',
                 'max_tokens': 10,
                 'temperature': 0,
             })],
            capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout)
        text = data['choices'][0]['text']
        print(f'  Completion: {text}')
        assert len(text) > 0, 'Empty completion'
    finally:
        proc.kill()
        proc.wait()


def test_models_endpoint():
    """Test /v1/models endpoint."""
    proc = serve_v2.port_forward(
        'qwen2-5-7b-instruct', namespace=NAMESPACE, local_port=8879)
    try:
        time.sleep(3)
        result = subprocess.run(
            ['curl', '-s', 'http://localhost:8879/v1/models'],
            capture_output=True, text=True, timeout=15)
        data = json.loads(result.stdout)
        models = [m['id'] for m in data['data']]
        print(f'  Models: {models}')
        assert 'Qwen/Qwen2.5-7B-Instruct' in models
    finally:
        proc.kill()
        proc.wait()


def test_status():
    """Test status command."""
    statuses = serve_v2.status('qwen2-5-7b-instruct', namespace=NAMESPACE)
    assert len(statuses) > 0, 'No services found'
    svc = statuses[0]
    print(f'  Service: {svc["name"]}')
    print(f'  Status: {svc["status"]}')
    print(f'  Replicas: {svc["replicas"]}')
    assert svc['ready'], f'Service not ready: {svc["status"]}'
    assert svc['name'] == 'qwen2-5-7b-instruct'


def test_status_with_metrics():
    """Test status with Prometheus metrics."""
    serve_v2.print_status(namespace=NAMESPACE)


def test_endpoint():
    """Test endpoint retrieval."""
    ep = serve_v2.endpoint('qwen2-5-7b-instruct', namespace=NAMESPACE)
    print(f'  Endpoint: {ep}')
    assert ep is not None, 'No endpoint found'
    assert '8000' in ep


def test_logs():
    """Test log retrieval."""
    log_output = serve_v2.logs(
        'qwen2-5-7b-instruct', namespace=NAMESPACE, tail=5)
    print(f'  Log lines: {len(log_output.split(chr(10)))}')
    assert len(log_output) > 0, 'No logs returned'
    assert 'vllm' in log_output.lower() or 'INFO' in log_output


def test_teardown():
    """Tear down the service."""
    result = serve_v2.down('qwen2-5-7b-instruct', namespace=NAMESPACE)
    assert result, 'Teardown failed'
    # Verify no pods remain
    time.sleep(5)
    statuses = serve_v2.status('qwen2-5-7b-instruct', namespace=NAMESPACE)
    assert len(statuses) == 0, f'Resources still exist: {statuses}'


# ---- Main ----

if __name__ == '__main__':
    print('SkyServe v2 End-to-End Test')
    print(f'Namespace: {NAMESPACE}')
    print(f'Spec: {SPEC_PATH}')
    start = time.time()

    # Ensure clean state
    try:
        serve_v2.down('qwen2-5-7b-instruct', namespace=NAMESPACE)
    except Exception:
        pass

    run('1. Prerequisites', test_prereqs)
    run('2. Spec Parsing', test_spec_parse)
    run('3. Deploy Model', test_deploy)
    run('4. Wait for Ready', test_wait_for_ready)
    run('5. Chat Completion', test_query_chat)
    run('6. Text Completion', test_query_completion)
    run('7. Models Endpoint', test_models_endpoint)
    run('8. Status', test_status)
    run('9. Status with Metrics', test_status_with_metrics)
    run('10. Endpoint', test_endpoint)
    run('11. Logs', test_logs)
    run('12. Teardown', test_teardown)

    elapsed = int(time.time() - start)
    print(f'\n{"=" * 60}')
    print(f'RESULTS: {passed} passed, {failed} failed ({elapsed}s)')
    print(f'{"=" * 60}')
    sys.exit(1 if failed > 0 else 0)
