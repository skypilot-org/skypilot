"""
Utility functions and configuration for SkyPilot operations.
"""

import io
import sys
import time
from typing import Dict, Optional, Tuple

import requests

import sky

# Configuration constants
API_TOKEN = 'skypilot-eval-token'  # vLLM accepts any string as API key
SERVE_TEMPLATE = 'configs/templates/serve-vllm.yaml'
EVAL_CONFIG = 'configs/eval_config.yaml'
MODEL_READY_TIMEOUT = 600  # 10 minutes
ENDPOINT_VERIFY_RETRIES = 3
DEFAULT_PORT = 8000


def check_cluster_ready(cluster_name: str,
                        api_token: str) -> Tuple[bool, Optional[str]]:
    """Check if cluster exists and model is already serving.
    
    Returns:
        (exists, endpoint): True if cluster exists and serving, endpoint URL if available
    """
    cluster_exists = False
    endpoint_url = None
    try:
        # Check cluster status
        status = sky.status(cluster_names=[cluster_name])
        result = sky.get(status)
        if result:
            cluster_exists = True
    except Exception as e:
        print(f"Error checking cluster {cluster_name}: {e}")
        cluster_exists = False

    if cluster_exists:
        try:
            # Check if endpoint is available
            port = 8000
            endpoint_result = sky.endpoints(cluster_name, port)
            endpoint = sky.get(endpoint_result)

            if endpoint and str(port) in endpoint:
                cur_endpoint_url = endpoint[str(port)]
                # Verify the endpoint is actually serving
                if verify_endpoint(cur_endpoint_url, api_token):
                    endpoint_url = cur_endpoint_url
        except Exception as e:
            print(f"Error checking endpoint for cluster {cluster_name}: {e}")
            endpoint_url = None

    return cluster_exists, endpoint_url


def wait_for_model_ready(cluster_name: str,
                         job_id: str,
                         timeout: int = 600) -> bool:
    """Wait for model server to be ready by checking logs."""
    start_time = time.time()
    check_interval = 5
    dots = 0

    while time.time() - start_time < timeout:
        try:
            # Create a string buffer to capture logs
            output_buffer = io.StringIO()

            # Get logs from the cluster using Sky Python API
            sky.tail_logs(cluster_name,
                          job_id=job_id,
                          follow=False,
                          tail=100,
                          output_stream=output_buffer)

            logs = output_buffer.getvalue()
            output_buffer.close()

            if logs and 'Application startup complete.' in logs:
                print(f"\r  ✅ {cluster_name} is ready!                    ")
                return True

            # Show progress indicator
            elapsed = int(time.time() - start_time)
            dots = (dots + 1) % 4
            progress = "." * dots + " " * (3 - dots)
            print(
                f'\r  ⏳ {cluster_name}: waiting for inference engine to start'
                f'{progress} ({elapsed}s)',
                end="")
            sys.stdout.flush()

            time.sleep(check_interval)
        except Exception as e:
            print(f"\r  ⚠️  Error checking {cluster_name}: {e}          ")
            time.sleep(check_interval)

    print(f"\r  ⏱️  {cluster_name}: timeout after {timeout}s          ")
    return False


def verify_endpoint(endpoint: str,
                    api_token: str,
                    max_retries: int = 3) -> bool:
    """Verify that the model endpoint is accessible."""
    url = f"http://{endpoint}/v1/models"
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


def get_cluster_endpoint(cluster_name: str, port: int = 8000) -> Optional[str]:
    """Get the endpoint for a cluster."""
    try:
        endpoint_result = sky.endpoints(cluster_name, port)
        endpoint = sky.get(endpoint_result)

        if endpoint and str(port) in endpoint:
            return endpoint[str(port)]
    except Exception:
        pass

    return None


def get_model_path_and_mounts(source: str) -> Tuple:
    """
    Determine model path, file mounts, and volumes based on model source.
    
    Returns:
        (model_path, file_mounts_dict, volumes_dict)
    """
    if source.startswith('hf://'):
        # Extract HuggingFace model ID: hf://org/model
        model_id = source[len('hf://'):]
        return model_id, None, None

    if source.startswith('ollama://'):
        # Extract Ollama model ID: ollama://model:tag
        model_id = source[len('ollama://'):]
        return model_id, None, None

    if source.startswith(('s3://', 'gs://')):
        # Extract bucket name and path: s3://bucket/path or gs://bucket/path
        prefix_len = len('s3://') if source.startswith('s3://') else len(
            'gs://')
        path = source[prefix_len:]
        mount_path = f"/buckets/{path}"

        # Create proper SkyPilot storage configuration for S3/GCS
        file_mounts = {mount_path: source}
        return mount_path, file_mounts, None

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
