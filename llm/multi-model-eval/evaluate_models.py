#!/usr/bin/env python3
"""
Evaluate multiple trained models using Promptfoo and SkyPilot.

Example usage:
    python evaluate_models.py
"""

import subprocess
import time
from typing import Dict, List
import uuid

import yaml

import sky

# Configuration
API_TOKEN = uuid.uuid4().hex
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
        'API_TOKEN': API_TOKEN
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


def launch_models_parallel(models: List[Dict]) -> List[Dict]:
    """Launch all models in parallel using futures."""
    print(f"\n📋 Launching {len(models)} models in parallel...")
    
    # Prepare and launch all models in parallel
    launch_requests = {}
    for model in models:
        try:
            task, cluster_name, name = prepare_model_task(model)
            print(f"🚀 Launching {name}...")
            request_id = sky.launch(task, cluster_name=cluster_name)
            launch_requests[name] = {
                'request_id': request_id,
                'cluster_name': cluster_name
            }
        except Exception as e:
            print(f"❌ Failed to launch {model['name']}: {e}")
    
    # Wait for all launches to complete
    print("\n⏳ Waiting for all clusters to launch...")
    launched_models = []
    
    for name, info in launch_requests.items():
        try:
            # Get the launch result
            result = sky.get(info['request_id'])
            print(f"✅ {name} launched successfully")
            
            # Get cluster status to find endpoint
            status_request = sky.status(cluster_names=[info['cluster_name']])
            cluster_info = sky.get(status_request)
            
            if cluster_info and len(cluster_info) > 0:
                handle = cluster_info[0].get('handle')
                if handle and hasattr(handle, 'head_ip'):
                    ip = handle.head_ip
                    endpoint = f"{ip}:8000/v1"
                    print(f"📡 {name} endpoint: http://{endpoint}")
                    
                    launched_models.append({
                        'name': name,
                        'cluster_name': info['cluster_name'],
                        'endpoint': endpoint
                    })
        except Exception as e:
            print(f"❌ {name} failed: {e}")
    
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
        print(f"❌ Evaluation failed: {result.stderr}")


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
