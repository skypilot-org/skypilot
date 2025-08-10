#!/usr/bin/env python3
"""
Evaluate multiple trained models using Promptfoo and SkyPilot.

Example usage:
    python evaluate_models.py
    python evaluate_models.py --config models_config_test.yaml
"""

import argparse
import os
import subprocess
from typing import Dict, List

import yaml

import sky
import utils


def load_models_config() -> Dict:
    """Load model configurations."""
    with open(utils.MODELS_CONFIG, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def prepare_model_task(model: Dict, cluster_prefix: str = "eval") -> tuple:
    """Prepare a model task for launching."""
    name = model['name']
    cluster_name = f"{cluster_prefix}-{name}"
    
    # Get model path, mounts, and volumes
    model_path, file_mounts, volumes = utils.get_model_path_and_mounts(
        model['source']
    )
    
    # Load task template - this includes all defaults (resources, envs, setup, run, etc.)
    task = sky.Task.from_yaml(utils.SERVE_TEMPLATE)
    task.name = f"serve-{name}"
    
    # Override environment variables
    task.update_envs({
        'MODEL_PATH': model_path,
        'API_TOKEN': utils.API_TOKEN,
        'HF_TOKEN': os.environ.get('HF_TOKEN', None)
    })
    
    # Override resources only if explicitly specified in model config
    # The template already has default resources loaded
    if 'accelerators' in model:
        # Update each resource in the set with the new accelerators
        updated_resources = []
        for resource in task.resources:
            updated_resources.append(resource.copy(accelerators=model['accelerators']))
        task.set_resources(updated_resources)
    
    # Set file mounts if needed
    if file_mounts:
        task.set_file_mounts(file_mounts)
    
    # Set volumes if needed (for Kubernetes)
    if volumes:
        task.set_volumes(volumes)
    
    return task, cluster_name, name




def launch_models_parallel(models: List[Dict], cluster_prefix: str = "eval") -> List[Dict]:
    """Launch all models in parallel using futures."""
    print(f"\n{'='*60}")
    print(f"🚀 LAUNCHING {len(models)} MODELS IN PARALLEL")
    print(f"{'='*60}\n")
    
    # Check existing clusters and prepare launch requests
    launch_requests = {}
    existing_models = []
    
    for i, model in enumerate(models, 1):
        try:
            task, cluster_name, name = prepare_model_task(model, cluster_prefix)
            
            # Determine the model ID that vLLM will use
            # Get the model path for all sources
            actual_model_id, _, _ = utils.get_model_path_and_mounts(
                model.get('source')
            )
            
            # Check if cluster already exists and is serving
            exists, _ = utils.check_cluster_ready(cluster_name, utils.API_TOKEN)
            
            if exists:
                print(f"[{i}/{len(models)}] {name}: cluster exists")
                # Cancel any running jobs before re-launching
                try:
                    print(f"  Cancelling existing jobs if any on {cluster_name}...")
                    cancel_request = sky.cancel(cluster_name, all=True)
                    sky.get(cancel_request)
                    print(f"  ✓ Cancelled existing jobs")
                except Exception as e:
                    print(f"  ⚠️  Could not cancel jobs: {e}")
                
                print(f"  Re-launching task...")
                request_id = sky.launch(task, cluster_name=cluster_name, fast=True)
                launch_requests[name] = {
                    'request_id': request_id,
                    'cluster_name': cluster_name,
                    'model': model,
                    'model_path': actual_model_id
                }
            else:
                print(f"[{i}/{len(models)}] Launching {name}...")
                request_id = sky.launch(task, cluster_name=cluster_name, fast=True)
                launch_requests[name] = {
                    'request_id': request_id,
                    'cluster_name': cluster_name,
                    'model': model,
                    'model_path': actual_model_id
                }
        except Exception as e:
            print(f"  ❌ Failed to launch: {e}")
    
    # Combine existing models with newly launched ones
    launched_models = existing_models.copy()
    
    if launch_requests:
        # Wait for all launches to complete
        print(f"\n{'─'*60}")
        print("⏳ WAITING FOR CLUSTERS TO PROVISION")
        print(f"{'─'*60}\n")
        
        for name, info in launch_requests.items():
            try:
                # Get the launch result
                result = sky.get(info['request_id'])
                job_id = result[0]
                print(f"  ✅ {name}: cluster provisioned")
                
                # Get cluster status to find endpoint
                endpoint = utils.get_cluster_endpoint(info['cluster_name'])
                
                if endpoint:
                    print(f"Endpoint: {endpoint}")
                    launched_models.append({
                        'name': name,
                        'cluster_name': info['cluster_name'],
                        'endpoint': endpoint,
                        'source': info['model'].get('source', 'unknown'),
                        'job_id': str(job_id),
                        'model_path': info.get('model_path')
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
            # Skip waiting for existing models that are already verified
            if model['job_id'] == 'existing':
                print(f"  ✅ {model['name']}: already serving (skipping wait)")
                ready_models.append(model)
            elif utils.wait_for_model_ready(model['cluster_name'], job_id=model['job_id']):
                # Verify endpoint is accessible
                if utils.verify_endpoint(model['endpoint'], utils.API_TOKEN):
                    print(f"  🌐 {model['name']}: endpoint verified at http://{model['endpoint']}")
                    if model.get('model_path'):
                        print(f"      Using model: {model['model_path']}")
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
            # Get model ID - use the actual model path from server if available
            model_id = model.get('model_path', 'auto')
            
            # Use openai provider with custom endpoint
            providers.append({
                'id': f'openai:chat:{model_id}',
                'label': model["name"],
                'config': {
                    'apiBaseUrl': f"http://{model['endpoint']}/v1",
                    'apiKey': utils.API_TOKEN,
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
    
    # Set environment variable to disable SSL verification for local endpoints
    env = os.environ.copy()
    env['NODE_TLS_REJECT_UNAUTHORIZED'] = '0'
    
    result = subprocess.run(
        ['promptfoo', 'eval', '-c', config_path, '--no-progress-bar'],
        capture_output=True,
        text=True,
        check=False,
        env=env
    )
    
    if result.returncode == 0:
        print("✅ Evaluation complete!")
        print("\nView results: promptfoo view")
    else:
        print(f'❌ Evaluation failed: {result.returncode} {result.stdout}\n'
              f'{result.stderr}')


def get_existing_clusters(models: List[Dict], cluster_prefix: str = "eval") -> List[Dict]:
    """Get endpoints for existing clusters without launching."""
    print(f"\n{'='*60}")
    print(f"🔍 CHECKING EXISTING CLUSTERS")
    print(f"{'='*60}\n")
    
    existing_models = []
    for i, model in enumerate(models, 1):
        name = model['name']
        cluster_name = f"{cluster_prefix}-{name}"
        
        # Get the model path for all sources
        actual_model_path, _, _ = utils.get_model_path_and_mounts(
            model.get('source')
        )
        
        print(f"[{i}/{len(models)}] Checking {name}...")
        
        # Check if cluster exists and get endpoint
        exists, endpoint = utils.check_cluster_ready(cluster_name, utils.API_TOKEN)
        
        if exists and endpoint:
            print(f"  ✅ Found at {endpoint}")
            existing_models.append({
                'name': name,
                'cluster_name': cluster_name,
                'endpoint': endpoint,
                'source': model.get('source', 'unknown'),
                'model_path': actual_model_path
            })
        else:
            print(f"  ❌ Not found or not ready")
    
    return existing_models


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


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Evaluate multiple models using Promptfoo and SkyPilot',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Launch clusters and run evaluation (default)
  python evaluate_models.py
  
  # Use custom config file
  python evaluate_models.py --config models_config_test.yaml
  
  # To skip launch or cleanup, edit the config file:
  # skip_launch: true      # Use existing clusters
  # cleanup_on_complete: false  # Keep clusters running
        """
    )
    
    parser.add_argument(
        '--config',
        default=utils.MODELS_CONFIG,
        help=f'Model configuration file (default: {utils.MODELS_CONFIG})'
    )
    
    return parser.parse_args()


def main():
    """Main workflow."""
    # Parse arguments
    args = parse_arguments()
    
    print("🎯 Multi-Model Evaluation with Parallel Launch")
    print("=" * 45)
    
    if args.config != utils.MODELS_CONFIG:
        print(f"📋 Using config: {args.config}")
    
    # Load configuration
    with open(args.config, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    models = config['models']
    skip_launch = config.get('skip_launch', False)
    cleanup_on_complete = config.get('cleanup_on_complete', True)
    cluster_prefix = config.get('cluster_prefix', 'eval')
    
    # Show configuration settings
    print(f"📛 Using cluster prefix: '{cluster_prefix}'")
    if skip_launch:
        print("⏭️  Skipping cluster launch (using existing clusters)")
    if not cleanup_on_complete:
        print("🔒 Clusters will not be terminated after evaluation")
    
    # Either get existing clusters or launch new ones
    if skip_launch:
        launched_models = get_existing_clusters(models, cluster_prefix)
        if not launched_models:
            print("\n❌ No existing clusters found. Please launch clusters first or set skip_launch: false")
            return
        print(f"\n🎉 Found {len(launched_models)}/{len(models)} existing clusters")
    else:
        # Launch all models in parallel
        launched_models = launch_models_parallel(models, cluster_prefix)
        if not launched_models:
            print("\n❌ No models launched successfully")
            return
        print(f"\n🎉 Successfully launched {len(launched_models)}/{len(models)} models")
    
    try:
        # Create and run evaluation
        create_evaluation_config(launched_models, 'promptfoo_config.yaml')
        run_evaluation('promptfoo_config.yaml')
        
    finally:
        # Cleanup based on config settings
        if cleanup_on_complete and not skip_launch:
            cleanup_clusters(launched_models)


if __name__ == '__main__':
    main()
