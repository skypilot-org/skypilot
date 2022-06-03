import colorama
import copy
import json
import os
import subprocess
import tempfile
from rich import progress as rich_progress
from typing import Any, Dict, List

from sky import data
from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky.backends import backend_utils
from sky.benchmark import benchmark_state
from sky.skylet import log_lib

logger = sky_logging.init_logger(__name__)

SKY_LOCAL_BENCHMARK_DIR = os.path.expanduser('~/.sky/benchmarks')
SKY_REMOTE_BENCHMARK_DIR = '~/.sky_benchmark_dir'
SKY_REMOTE_BENCHMARK_DIR_SYMLINK = '~/sky_benchmark_dir'

SKY_BENCHMARK_BUCKET = 'sky-benchmark'
SKY_BENCHMARK_BUCKET_TYPE = data.StoreType.S3 # FIXME


def _generate_cluster_names(benchmark: str, num_clusters: int) -> List[str]:
    names = []
    for i in range(num_clusters):
        name = f'{benchmark}-{i}'
        if global_user_state.get_cluster_from_name(name) is not None:
            raise ValueError(f'Cluster name {name} is taken.')
        names.append(name)
    return names


def _launch_with_log(cluster: str, cmd: List[str]) -> None:
    # TODO: show well-organized optimizer messages.
    log_lib.run_with_log(
        cmd,
        log_path='/dev/null',
        stream_logs=True,
        prefix=f'{colorama.Fore.MAGENTA}({cluster}){colorama.Style.RESET_ALL} ',
        skip_lines=[
            'optimizer.py',
            'Tip: ',
        ],  # FIXME: Use regex
        end_streaming_at='Job submitted with Job ID:',
    )


class BenchmarkController:

    @staticmethod
    def launch(benchmark: str, config: Dict[str, Any], cl_args: Dict[str, Any], candidates: List[Dict[str, Any]]) -> None:
        # Create a Sky storage to save the benchmark logs.
        storage = data.Storage(SKY_BENCHMARK_BUCKET, source=None, persistent=True)
        storage.add_store(SKY_BENCHMARK_BUCKET_TYPE)

        # Generate a config for each cluster.
        clusters = _generate_cluster_names(benchmark, len(candidates))
        candidate_configs = []
        for cluster, candidate in zip(clusters, candidates):
            # Re-override the config with each candidate config.
            candidate_config = copy.deepcopy(config)
            if 'resources' not in candidate_config:
                candidate_config['resources'] = {}
            if 'candidates' in candidate_config['resources']:
                del candidate_config['resources']['candidates']
            candidate_config['resources'].update(candidate)

            # Mount the benchmark bucket to SKY_BENCHMARK_DIR.
            if 'file_mounts' not in candidate_config:
                candidate_config['file_mounts'] = {}
            candidate_config['file_mounts'][SKY_REMOTE_BENCHMARK_DIR] = {
                'name': SKY_BENCHMARK_BUCKET,
                'mode': 'MOUNT',
            }

            # Create a sym link to a directory in the benchmark bucket.
            benchmark_dir = os.path.join(SKY_REMOTE_BENCHMARK_DIR, benchmark, cluster)
            if 'setup' not in candidate_config:
                candidate_config['setup'] = ''
            candidate_config['setup'] = (f'mkdir -p {benchmark_dir}; '
                + f'ln -s {benchmark_dir} {SKY_REMOTE_BENCHMARK_DIR_SYMLINK}; '
                + candidate_config['setup'])
            candidate_configs.append(candidate_config)

        # Generate a temporary yaml file for each cluster.
        yaml_fds = []
        for cluster, candidate_config in zip(clusters, candidate_configs):
            f = tempfile.NamedTemporaryFile('w', prefix=f'{cluster}-', suffix='.yaml')
            backend_utils.dump_yaml(f.name, candidate_config)
            yaml_fds.append(f)

        # Generate a common launch command.
        cmd = ['-d', '-y']
        for arg_name, arg in cl_args.items():
            if isinstance(arg, list):
                # 'env' arguments.
                for v in arg:
                    cmd += [f'--{arg_name}', str(v)]
            else:
                cmd += [f'--{arg_name}', str(arg)]

        # Generate a launch command for each cluster.
        launch_cmds = [
            ['sky', 'launch', yaml_fd.name, '-c', cluster] + cmd
            for yaml_fd, cluster in zip(yaml_fds, clusters)
        ]

        # Launch clusters in parallel.
        backend_utils.run_in_parallel(
            lambda arg: _launch_with_log(*arg),
            list(zip(clusters, launch_cmds)))

        # Delete the temporary yaml files.
        for f in yaml_fds:
            f.close()

        # If at least one cluster has been launched successfully,
        # add the benchmark to the state.
        benchmark_created = False
        for cluster in clusters:
            record = global_user_state.get_cluster_from_name(cluster)
            if record is not None:
                if not benchmark_created:
                    benchmark_state.add_benchmark(
                        benchmark, task_name=config.get('name', None))
                    benchmark_created = True
                global_user_state.set_cluster_benchmark_name(cluster, benchmark)
                benchmark_state.add_benchmark_result(benchmark, record['handle'])

    @staticmethod
    def download_logs(benchmark: str, clusters: List[str]):
        plural = 's' if len(clusters) > 1 else ''
        progress = rich_progress.Progress(transient=True,
                                        redirect_stdout=False,
                                        redirect_stderr=False)
        task = progress.add_task(
            f'[bold cyan]Downloading {len(clusters)} benchmark log{plural}[/]',
            total=len(clusters))

        storage = data.Storage(SKY_BENCHMARK_BUCKET, source=None, persistent=True)
        bucket = storage.stores[SKY_BENCHMARK_BUCKET_TYPE]

        def _download_log(cluster: str):
            local_dir = os.path.join(SKY_LOCAL_BENCHMARK_DIR, benchmark, cluster)
            os.makedirs(local_dir, exist_ok=True)
            try:
                # FIXME: Use download_remote_dir method instead.
                for file in ['config.json', 'timestamps.log']:
                    bucket._download_file(
                        f'{benchmark}/{cluster}/{file}',
                        f'{local_dir}/{file}',
                    )
                progress.update(task, advance=1)
            except exceptions.CommandError as e:
                logger.error(
                    f'Command failed with code {e.returncode}: {e.command}')
                logger.error(e.error_msg)

        with progress:
            backend_utils.run_in_parallel(_download_log, clusters)
            progress.live.transient = False
            progress.refresh()

    @staticmethod
    def parse_logs(benchmark: str, clusters: List[str]):
        for cluster in clusters:
            local_dir = os.path.join(SKY_LOCAL_BENCHMARK_DIR, benchmark, cluster)
            config_file = os.path.join(local_dir, 'config.json')
            with open(config_file, 'r') as f:
                config = json.load(f)
            start_ts = config['start_ts']
            total_steps = config['total_steps']

            # FIXME
            timestamp_log = os.path.join(local_dir, 'timestamps.log')
            timestamps = []
            with open(timestamp_log, 'rb') as f:
                while True:
                    b = f.read(4)
                    ts = int.from_bytes(b, 'big')
                    if ts == 0:
                        # EOF
                        break
                    else:
                        timestamps.append(ts)

            num_steps = int(len(timestamps) / 2)
            steps_per_sec = 0
            for i in range(num_steps):
                start = timestamps[2 * i]
                end = timestamps[2 * i + 1]
                steps_per_sec += end - start
            steps_per_sec /= num_steps

            record = benchmark_state.BenchmarkRecord(
                num_steps,
                steps_per_sec,
                total_steps,
                start_ts,
                timestamps[0],
                timestamps[-1],
            )
            benchmark_state.update_benchmark_result(benchmark, cluster, record)

    @staticmethod
    def remove_logs(benchmark: str):
        log_dir = os.path.join(SKY_LOCAL_BENCHMARK_DIR, benchmark)
        subprocess.run(['rm', '-rf', log_dir], check=False)
        # TODO: remove the logs in the bucket.
