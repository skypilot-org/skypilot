import json
import os
import subprocess
from typing import List, Dict
from sky import global_user_state, sky_logging
from sky import backends
from sky import exceptions

from sky.backends import backend_utils

logger = sky_logging.init_logger(__name__)

SKY_CLOUD_BENCHMARK_DIR = '~/sky_benchmark_dir'
SKY_CLOUD_BENCHMARK_SUMMARY = os.path.join(SKY_CLOUD_BENCHMARK_DIR, 'summary.json')
SKY_LOCAL_BENCHMARK_DIR = os.path.expanduser('~/.sky/benchmarks')

def get_benchmark_summaries(benchmark: str, logger_name: str, clusters: List[str]) -> Dict[str, Dict[str, int]]:
    def _get_summary(cluster: str):
        handle = global_user_state.get_handle_from_cluster_name(cluster)
        backend = backend_utils.get_backend_from_handle(handle)
        assert isinstance(backend, backends.CloudVmRayBackend)

        if logger_name == 'wandb':
            log_dir = os.path.join(SKY_CLOUD_BENCHMARK_DIR, 'wandb')
        elif logger_name == 'tensorboard':
            log_dir = SKY_CLOUD_BENCHMARK_DIR

        download_dir = os.path.join(SKY_LOCAL_BENCHMARK_DIR, benchmark, cluster)
        os.makedirs(download_dir, exist_ok=True)
        try:
            backend.get_benchmark_summary(
                handle, log_dir, SKY_CLOUD_BENCHMARK_SUMMARY, download_dir, logger_name)
        except exceptions.CommandError as e:
            logger.error(
                f'Command failed with code {e.returncode}: {e.command}')
            logger.error(e.error_msg)

    with backend_utils.safe_console_status('[bold cyan]Downloading logs[/]'): # FIXME
        backend_utils.run_in_parallel(_get_summary, clusters)

    summaries = {}
    for cluster in clusters:
        summary_path = os.path.join(SKY_LOCAL_BENCHMARK_DIR, benchmark, cluster, 'summary.json')
        if os.path.exists(summary_path):
            with open(summary_path, 'r') as f:
                summary = json.load(f)
            summaries[cluster] = summary
    return summaries


def remove_benchmark_logs(benchmark: str):
    log_dir = os.path.join(SKY_LOCAL_BENCHMARK_DIR, benchmark)
    subprocess.run(['rm', '-rf', log_dir], check=False)
