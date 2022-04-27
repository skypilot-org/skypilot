import glob
import os
import subprocess
from typing import Tuple

from sky.backends import backend_utils
from sky.skylet import log_lib


# FIXME
SKY_CLOUD_BENCHMARK_DIR = '~/sky_benchmark_dir'
SKY_LOCAL_BENCHMARK_DIR = os.path.expanduser('~/.sky/benchmarks')


def _download_benchmark_log(benchmark: str, cluster_name: str, logger_name: str) -> None:
    """Download the benchmark logs from the cluster."""
    # FIXME: Download only the log file, not others.
    cmd = [
        'rsync',
        '-Pavz',
        # FIXME: use ssh hostname instead.
    ]
    if logger_name == 'wandb':
        cmd += [
            f'{cluster_name}:{SKY_CLOUD_BENCHMARK_DIR}/wandb/latest-run/files/wandb-summary.json',
            f'{SKY_LOCAL_BENCHMARK_DIR}/{benchmark}/{cluster_name}/',
        ]
    elif logger_name == 'tensorboard':
        cmd += [
            f'{cluster_name}:{SKY_CLOUD_BENCHMARK_DIR}/',
            f'{SKY_LOCAL_BENCHMARK_DIR}/{benchmark}/{cluster_name}/',
        ]
    else:
        assert False, f'Unknown logger: {logger_name}.'

    returncode = log_lib.run_with_log(
        cmd, log_path='/dev/null', stream_logs=False, shell=False)
    backend_utils.handle_returncode(
        returncode,
        cmd,
        f'Failed to download logs from {cluster_name}.',
        raise_error=True,
    )


def download_logs(benchmark, logger_name, clusters):
    benchmark_dir = f'{SKY_LOCAL_BENCHMARK_DIR}/{benchmark}'
    subprocess.run(['mkdir', '-p', benchmark_dir], check=False)

    # Download the log files.
    with backend_utils.safe_console_status('[bold cyan]Downloading logs[/]'):
        backend_utils.run_in_parallel(
            lambda cluster: _download_benchmark_log(benchmark, cluster, logger_name), clusters)


def parse_tensorboard(log_dir: str) -> Tuple[int, int, int, int]:
    import pandas as pd
    from tensorboard.backend.event_processing import event_accumulator

    event_files = glob.glob(os.path.join(log_dir, '*.tfevents.*'))
    if not event_files:
        raise ValueError(f'No tensorboard logs found in {log_dir}.')

    event_file = event_files[-1]  # FIXME

    ea = event_accumulator.EventAccumulator(
        event_file, size_guidance={event_accumulator.SCALARS: 0})
    ea.Reload()
    scalar = ea.Tags()['scalars'][0]
    df = pd.DataFrame(ea.Scalars(scalar))
    timestamps = df['wall_time']

    start_ts = int(os.path.basename(event_file).split('.')[3])
    first_ts = int(timestamps.iloc[0])
    last_ts = int(timestamps.iloc[-1])
    iters = len(timestamps)
    return start_ts, first_ts, last_ts, iters


def parse_wandb(log_dir: str) -> Tuple[int, int, int, int]:
    import pandas as pd

    wandb_summary = os.path.join(log_dir, 'wandb-summary.json')
    summary = pd.read_json(wandb_summary, lines=True)
    assert len(summary) == 1
    summary = summary.iloc[0]

    last_ts = summary['_timestamp']
    iters = summary['_step']
    start_ts = last_ts - summary['_runtime']
    first_ts = None  # run.scan_history(keys=['_timestamp'], max_step=1)
    return start_ts, first_ts, last_ts, iters
