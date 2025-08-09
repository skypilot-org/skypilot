#!/usr/bin/env python3
"""
Evaluate multiple trained models using Promptfoo and SkyPilot.

Example usage:
    python evaluate_models.py
"""

import uuid
import time
from typing import Dict, List
import concurrent.futures

import sky
import yaml
import subprocess


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
    Determine model path and file mounts based on model source.
    
    Returns:
        (model_path, file_mounts_dict)
    """
    if source == 'huggingface':
        return model_id, None
    
    elif source.startswith('s3://') or source.startswith('gs://'):
        # Cloud bucket - mount at /model
        return "/model", {'/model': source}
    
    elif source.startswith('volume://'):
        # SkyPilot volume - extract volume name and path
        parts = source.replace('volume://', '').split('/', 1)
        volume_name = parts[0]
        model_path = parts[1] if len(parts) > 1 else ''
        
        return f"/volumes/{model_path}", {'/volumes': volume_name}
    
    else:
        raise ValueError(f"Unknown source type: {source}")


def launch_model(model: Dict) -> Dict:
    """Launch a single model on SkyPilot."""
    name = model['name']
    cluster_name = f"eval-{name}"
    
    print(f"\n🚀 Launching {name}...")
    
    try:
        # Get model path and mounts
        model_path, file_mounts = get_model_path_and_mounts(
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
        
        # Launch cluster
        sky.launch(task, cluster_name=cluster_name)
        print(f"✅ Launched {cluster_name}")
        
        # Wait for model to load
        print("⏳ Waiting for model to load...")
        time.sleep(60)
        
        # Get endpoint
        status = sky.status(cluster_name)
        if status and status[0].get('handle'):
            ip = status[0]['handle'].head_ip
            endpoint = f"{ip}:8000/v1"
            print(f"📡 Endpoint: http://{endpoint}")
            
            return {
                'name': name,
                'cluster_name': cluster_name,
                'endpoint': endpoint
            }
    
    except Exception as e:
        print(f"❌ Failed to launch {name}: {e}")
        return None


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
                sky.down(model['cluster_name'])
                print(f"  ✓ Terminated {model['cluster_name']}")
            except Exception as e:
                print(f"  ✗ Failed to terminate {model['cluster_name']}: {e}")


def main():
    """Main workflow."""
    print("🎯 Multi-Model Evaluation")
    print("=" * 25)
    
    # Load configuration
    config = load_models_config()
    models = config['models']
    
    # Launch models in parallel
    print(f"\n📋 Launching {len(models)} models...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(launch_model, m) for m in models]
        launched_models = [f.result() for f in futures if f.result()]
    
    if not launched_models:
        print("\n❌ No models launched successfully")
        return
    
    print(f"\n✅ Successfully launched {len(launched_models)} models")
    
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