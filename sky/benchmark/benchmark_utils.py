"""Functions to download and parse the benchmark data."""
import json
import os
from rich import progress as rich_progress
import subprocess
from typing import Dict, List

from sky import backends
from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky.backends import backend_utils

logger = sky_logging.init_logger(__name__)

SKY_CLOUD_BENCHMARK_DIR = '~/sky_benchmark_dir'
SKY_BENCHMARK_SUMMARY = '_summary.json'
SKY_CLOUD_BENCHMARK_SUMMARY = os.path.join(SKY_CLOUD_BENCHMARK_DIR,
                                           SKY_BENCHMARK_SUMMARY)
SKY_LOCAL_BENCHMARK_DIR = os.path.expanduser('~/.sky/benchmarks')


def get_benchmark_summaries(benchmark: str, 
                            clusters: List[str]) -> Dict[str, Dict[str, int]]:
    plural = 's' if len(clusters) > 1 else ''
    progress = rich_progress.Progress(transient=True,
                                      redirect_stdout=False,
                                      redirect_stderr=False)
    task = progress.add_task(
        f'[bold cyan]Downloading {len(clusters)} benchmark log{plural}[/]',
        total=len(clusters))

    # Generate a summary of the log in each cluster,
    # and download the summary to the local benchmark directory.
    def _get_summary(cluster: str):
        handle = global_user_state.get_handle_from_cluster_name(cluster)
        backend = backend_utils.get_backend_from_handle(handle)
        assert isinstance(backend, backends.CloudVmRayBackend)

        log_dir = SKY_CLOUD_BENCHMARK_DIR
        download_dir = os.path.join(SKY_LOCAL_BENCHMARK_DIR, benchmark, cluster)
        os.makedirs(download_dir, exist_ok=True)
        try:
            backend.get_benchmark_summary(handle, log_dir,
                                          SKY_CLOUD_BENCHMARK_SUMMARY,
                                          download_dir)
            progress.update(task, advance=1)
        except exceptions.CommandError as e:
            logger.error(
                f'Command failed with code {e.returncode}: {e.command}')
            logger.error(e.error_msg)

    with progress:
        backend_utils.run_in_parallel(_get_summary, clusters)
        progress.live.transient = False
        # Make sure the progress bar not mess up the terminal.
        progress.refresh()

    # Read the summaries from the locally saved files.
    summaries = {}
    for cluster in clusters:
        summary_path = os.path.join(SKY_LOCAL_BENCHMARK_DIR, benchmark, cluster,
                                    SKY_BENCHMARK_SUMMARY)
        if os.path.exists(summary_path):
            with open(summary_path, 'r') as f:
                summary = json.load(f)
            summaries[cluster] = summary
    return summaries


def remove_benchmark_logs(benchmark: str):
    log_dir = os.path.join(SKY_LOCAL_BENCHMARK_DIR, benchmark)
    subprocess.run(['rm', '-rf', log_dir], check=False)
