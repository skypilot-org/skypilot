#!/usr/bin/env python3
"""
Evaluate multiple trained models using Promptfoo and SkyPilot.

Example usage:
    python evaluate_models.py
"""

import subprocess
import time
import os
from typing import Dict, List
import uuid

import requests
import yaml

import sky

# Configuration
# API_TOKEN = uuid.uuid4().hex
API_TOKEN = 'default-api-token'
SERVE_TEMPLATE = 'templates/serve-model.yaml'
MODELS_CONFIG = 'models_config.yaml'


def load_models_config() -> Dict:
    """Load model configurations."""
    with open(MODELS_CONFIG, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def get_model_path_and_mounts(source: str, model_id: str = None) -> tuple:
    """
    Determine model path, file mounts, and volumes based on model source.
    
    Returns:
        (model_path, file_mounts_dict, volumes_dict)
    """
    if source == 'huggingface':
        return model_id, None, None
    
    if source.startswith(('s3://', 'gs://')):
        # Extract bucket name and path: s3://bucket/path or gs://bucket/path
        prefix_len = len('s3://') if source.startswith('s3://') else len('gs://')
        path_parts = source[prefix_len:].split('/', 1)
        bucket_name = path_parts[0]
        bucket_path = path_parts[1] if len(path_parts) > 1 else ''
        
        # Mount at unique path per bucket
        mount_path = f"/buckets/{bucket_name}"
        model_path = f"{mount_path}/{bucket_path}" if bucket_path else mount_path
        return model_path, {mount_path: source}, None
    
    if source.startswith('volume://'):
        # Extract volume name and path: volume://volume-name/path
        path_parts = source[len('volume://'):].split('/', 1)
        volume_name = path_parts[0]
        volume_path = path_parts[1] if len(path_parts) > 1 else ''
        
        # Mount at unique path per volume
        mount_path = f"/volumes/{volume_name}"
        model_path = f"{mount_path}/{volume_path}" if volume_path else mount_path
        return model_path, None, {mount_path: volume_name}
    
    raise ValueError(f"Unknown source type: {source}")


def prepare_model_task(model: Dict) -> tuple:
    """Prepare a model task for launching."""
    name = model['name']
    cluster_name = f"eval-{name}"
    
    # Get model path, mounts, and volumes
    model_path, file_mounts, volumes = get_model_path_and_mounts(
        model['source'], 
        model.get('model_id')
    )
    
    # Load task template
    task = sky.Task.from_yaml(SERVE_TEMPLATE)
    task.name = f"serve-{name}"
    
    # Set environment variables
    task.update_envs({
        'MODEL_PATH': model_path,
        'API_TOKEN': API_TOKEN,
        'HF_TOKEN': os.environ.get('HF_TOKEN', None)
    })
    
    # Set resources
    task.set_resources(
        sky.Resources(
            accelerators=model['accelerators'],
            ports=[8000]
        )
    )
    
    # Set file mounts if needed
    if file_mounts:
        task.set_file_mounts(file_mounts)
    
    # Set volumes if needed (for Kubernetes)
    if volumes:
        task.set_volumes(volumes)
    
    return task, cluster_name, name


def wait_for_model_ready(cluster_name: str, job_id: str, timeout: int = 300) -> bool:
    """Wait for model server to be ready by checking logs."""
    import io
    import sys
    
    start_time = time.time()
    check_interval = 5
    dots = 0
    
    while time.time() - start_time < timeout:
        try:
            # Create a string buffer to capture logs
            output_buffer = io.StringIO()
            
            # Get logs from the cluster using Sky Python API
            sky.tail_logs(cluster_name, job_id=job_id, follow=False, 
                         tail=100, output_stream=output_buffer)
            
            logs = output_buffer.getvalue()
            output_buffer.close()
            
            if logs and 'Application startup complete.' in logs:
                print(f"\r  ✅ {cluster_name} is ready!                    ")
                return True
            
            # Show progress indicator
            elapsed = int(time.time() - start_time)
            dots = (dots + 1) % 4
            progress = "." * dots + " " * (3 - dots)
            print(f"\r  ⏳ {cluster_name}: waiting for vLLM server to start{progress} ({elapsed}s)", end="")
            sys.stdout.flush()
            
            time.sleep(check_interval)
        except Exception as e:
            print(f"\r  ⚠️  Error checking {cluster_name}: {e}          ")
            time.sleep(check_interval)
    
    print(f"\r  ⏱️  {cluster_name}: timeout after {timeout}s          ")
    return False


def verify_endpoint(endpoint: str, api_token: str, max_retries: int = 3) -> bool:
    """Verify that the model endpoint is accessible."""
    url = f"http://{endpoint}/models"
    headers = {"Authorization": f"Bearer {api_token}"}
    
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=5)
            if response.status_code == 200:
                return True
        except:
            pass
        if attempt < max_retries - 1:
            time.sleep(2)
    return False


def launch_models_parallel(models: List[Dict]) -> List[Dict]:
    """Launch all models in parallel using futures."""
    print(f"\n{'='*60}")
    print(f"🚀 LAUNCHING {len(models)} MODELS IN PARALLEL")
    print(f"{'='*60}\n")
    
    # Prepare and launch all models in parallel
    launch_requests = {}
    for i, model in enumerate(models, 1):
        try:
            task, cluster_name, name = prepare_model_task(model)
            print(f"[{i}/{len(models)}] Launching {name}...")
            request_id = sky.launch(task, cluster_name=cluster_name, fast=True)
            launch_requests[name] = {
                'request_id': request_id,
                'cluster_name': cluster_name,
                'model': model
            }
        except Exception as e:
            print(f"  ❌ Failed to launch: {e}")
    
    if not launch_requests:
        return []
    
    # Wait for all launches to complete
    print(f"\n{'─'*60}")
    print("⏳ WAITING FOR CLUSTERS TO PROVISION")
    print(f"{'─'*60}\n")
    
    launched_models = []
    for name, info in launch_requests.items():
        try:
            # Get the launch result
            result = sky.get(info['request_id'])
            job_id = result[0]
            print(f"  ✅ {name}: cluster provisioned")
            
            # Get cluster status to find endpoint
            port = 8000
            endpoint = sky.get(sky.endpoints(info['cluster_name'], port))
            
            if endpoint:
                endpoint = endpoint[str(port)]
                print(f"Endpoint: {endpoint}")
                launched_models.append({
                    'name': name,
                    'cluster_name': info['cluster_name'],
                    'endpoint': endpoint,
                    'source': info['model'].get('source', 'unknown'),
                    'job_id': str(job_id)
                })
            else:
                print(f"  ⚠️  {name}: endpoint not found")
        except Exception as e:
            print(f"  ❌ {name}: provisioning failed - {e}")
    
    # Wait for all models to be ready
    if launched_models:
        print(f"\n{'─'*60}")
        print("🔄 WAITING FOR MODEL SERVERS TO START")
        print(f"{'─'*60}\n")
        
        ready_models = []
        for model in launched_models:
            if wait_for_model_ready(model['cluster_name'], job_id=model['job_id']):
                # Verify endpoint is accessible
                if verify_endpoint(model['endpoint'], API_TOKEN):
                    print(f"  🌐 {model['name']}: endpoint verified at http://{model['endpoint']}")
                    ready_models.append(model)
                else:
                    print(f"  ⚠️  {model['name']}: endpoint not accessible")
            else:
                print(f"  ⚠️  {model['name']}: server didn't start in time")
        
        return ready_models
    
    return launched_models


def create_evaluation_config(models: List[Dict], output_path: str):
    """Create Promptfoo configuration."""
    # Build provider list
    providers = []
    for model in models:
        if model:
            providers.append({
                'id': f'openai:chat:{model["name"]}',
                'config': {
                    'baseUrl': f"http://{model['endpoint']}",
                    'apiKey': API_TOKEN,
                    'temperature': 0.7,
                    'max_tokens': 512
                }
            })
    
    # Define evaluation
    config = {
        'description': 'Model comparison',
        'providers': providers,
        'prompts': ["You are a helpful AI assistant. {{message}}"],
        'tests': [
            {
                'vars': {'message': 'What is quantum computing?'},
                'assert': [{'type': 'contains', 'value': 'quantum'}]
            },
            {
                'vars': {'message': 'Write a hello world in Python'},
                'assert': [{'type': 'contains', 'value': 'print'}]
            },
            {
                'vars': {'message': 'Explain machine learning'},
                'assert': [{'type': 'contains', 'value': 'learn'}]
            }
        ],
        'outputPath': './results.json'
    }
    
    with open(output_path, 'w', encoding='utf-8') as f:
        yaml.dump(config, f, default_flow_style=False)
    
    print(f"\n📝 Created evaluation config for {len(providers)} models")


def run_evaluation(config_path: str):
    """Run Promptfoo evaluation."""
    print("\n🔍 Running evaluation...")
    
    result = subprocess.run(
        ['promptfoo', 'eval', '-c', config_path, '--no-progress-bar'],
        capture_output=True,
        text=True,
        check=False
    )
    
    if result.returncode == 0:
        print("✅ Evaluation complete!")
        print("\nView results: promptfoo view")
    else:
        print(f'❌ Evaluation failed: {result.returncode} {result.stdout}\n'
              f'{result.stderr}')


def cleanup_clusters(models: List[Dict]):
    """Terminate all clusters."""
    print("\n🧹 Cleaning up...")
    
    for model in models:
        if model:
            try:
                request_id = sky.down(model['cluster_name'])
                result = sky.get(request_id)
                print(f"  ✓ Terminated {model['cluster_name']}")
            except Exception as e:
                print(f"  ✗ Failed to terminate {model['cluster_name']}: {e}")


def main():
    """Main workflow."""
    print("🎯 Multi-Model Evaluation with Parallel Launch")
    print("=" * 45)
    
    # Check for custom config file from command line
    import sys
    config_file = MODELS_CONFIG
    if len(sys.argv) > 1 and '--config' in sys.argv:
        idx = sys.argv.index('--config')
        if idx + 1 < len(sys.argv):
            config_file = sys.argv[idx + 1]
            print(f"📋 Using config: {config_file}")
    
    # Load configuration
    with open(config_file, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    models = config['models']
    
    # Launch all models in parallel using futures
    launched_models = launch_models_parallel(models)
    
    if not launched_models:
        print("\n❌ No models launched successfully")
        return
    
    print(f"\n🎉 Successfully launched {len(launched_models)}/{len(models)} models")
    
    try:
        # Create and run evaluation
        create_evaluation_config(launched_models, 'promptfoo_config.yaml')
        run_evaluation('promptfoo_config.yaml')
        
    finally:
        # Cleanup
        if config.get('cleanup_on_complete', True):
            cleanup_clusters(launched_models)


if __name__ == '__main__':
    main()
