import colorama
import copy
import getpass
import json
import os
import prettytable
import subprocess
import tempfile
import uuid
from rich import progress as rich_progress
from typing import Any, Dict, List, Tuple

import sky
from sky import data
from sky import exceptions
from sky import global_user_state
from sky import resources as resources_lib
from sky import sky_logging
from sky.backends import backend_utils
from sky.benchmark import benchmark_state
from sky.skylet import log_lib
from sky.skylet.utils import log_utils

logger = sky_logging.init_logger(__name__)

_SKY_LOCAL_BENCHMARK_DIR = os.path.expanduser('~/.sky/benchmarks')
_SKY_REMOTE_BENCHMARK_DIR = '~/.sky/sky_benchmark_dir'
_SKY_REMOTE_BENCHMARK_DIR_SYMLINK = '~/sky_benchmark_dir'


def _generate_cluster_names(benchmark: str, num_clusters: int) -> List[str]:
    if num_clusters == 1:
        names = [f'benchmark-{benchmark}']
    else:
        names = [f'benchmark-{benchmark}-{i}' for i in range(num_clusters)]
    for name in names:
        if global_user_state.get_cluster_from_name(name) is not None:
            raise ValueError(f'Cluster name {name} is taken. '
                             'Try using a different benchmark name.')
    return names


def _get_optimized_resources(candidate_configs: List[Dict[str, Any]]) -> List['resources_lib.Resources']:
    candidate_configs = copy.deepcopy(candidate_configs)
    optimized_resources = []
    for config in candidate_configs:
        with sky.Dag() as dag:
            resources = config.get('resources', None)
            resources = sky.Resources.from_yaml_config(resources)
            task = sky.Task()
            task.set_resources({resources})

        dag = sky.optimize(dag, print_plan=False)
        task = dag.tasks[0]
        optimized_resources.append(task.best_resources)
    return optimized_resources


def _print_candidate_resources(clusters: List[str], config: Dict[str, Any], candidate_resources: List['resources_lib.Resources']) -> None:
    task_str = config.get('name', 'a task')
    num_nodes = config.get('num_nodes', 1)
    plural = 's' if num_nodes > 1 else ''
    logger.info(
        f'{colorama.Style.BRIGHT}Benchmarking {task_str} '
        f'on candidate resources ({num_nodes} node{plural}):'
        f'{colorama.Style.RESET_ALL}')

    columns = ['NAME', 'CLOUD', 'INSTANCE', 'ACCELERATORS', 'COST ($/hr)']
    table_kwargs = {
        'hrules': prettytable.FRAME,
        'vrules': prettytable.NONE,
        'border': True,
    }
    candidate_table = log_utils.create_table(columns, **table_kwargs)

    for cluster, resources in zip(clusters, candidate_resources):
        if resources.accelerators is None:
            accelerators = '-'
        else:
            accelerator, count = list(resources.accelerators.items())[0]
            accelerators = f'{accelerator}:{count}'
        cost = num_nodes * resources.get_cost(3600)
        row = [cluster, resources.cloud, resources.instance_type, accelerators, f'{cost:.2f}']
        candidate_table.add_row(row)
    logger.info(f'{candidate_table}\n')


def _get_benchmark_bucket() -> Tuple[str, str]:
    bucket_name, bucket_type = benchmark_state.get_benchmark_bucket()
    if bucket_name is not None:
        handle = global_user_state.get_handle_from_storage_name(bucket_name)
        if handle is not None:
            assert bucket_type is not None
            return bucket_name, bucket_type

    # Generate a bucket name.
    # TODO(woosuk): Use a more pleasant naming scheme.
    # TODO(woosuk): Ensure that the bucket name is globally unique.
    bucket_name = f'sky-benchmark-{uuid.uuid4().hex[:4]}-{getpass.getuser()}'

    # Select the bucket type.
    enabled_clouds = global_user_state.get_enabled_clouds()
    enabled_clouds = [str(cloud) for cloud in enabled_clouds]
    if 'AWS' in enabled_clouds:
        bucket_type = data.StoreType.S3.value
    elif 'GCP' in enabled_clouds:
        bucket_type = data.StoreType.GCS.value
    elif 'AZURE' in enabled_clouds:
        bucket_type = data.StoreType.AZURE.value
    else:
        raise ValueError('No cloud is enabled. '
                         'Please enable at least one cloud.')

    # Create a benchmark bucket.
    logger.info(f'Creating a bucket {bucket_name} to save the benchmark logs.')
    storage = data.Storage(bucket_name, source=None, persistent=True)
    storage.add_store(bucket_type)

    # Save the bucket name and type to the config.
    benchmark_state.set_benchmark_bucket(bucket_name, bucket_type)
    return bucket_name, bucket_type


def _launch_with_log(cluster: str, cmd: List[str]) -> None:
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
    def generate_configs(benchmark: str, config: Dict[str, Any], candidates: List[Dict[str, Any]]) -> Tuple[List[str], List[Dict[str, Any]]]:
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
            # The bucket name and type are specified at launch time.
            candidate_config['file_mounts'][_SKY_REMOTE_BENCHMARK_DIR] = {
                'name': '', 'mode': 'MOUNT', 'store': ''}

            # Create a sym link to a directory in the benchmark bucket.
            benchmark_dir = os.path.join(_SKY_REMOTE_BENCHMARK_DIR, benchmark, cluster)
            if 'setup' not in candidate_config:
                candidate_config['setup'] = ''
            candidate_config['setup'] = (f'mkdir -p {benchmark_dir}; '
                + f'ln -s {benchmark_dir} {_SKY_REMOTE_BENCHMARK_DIR_SYMLINK}; '
                + candidate_config['setup'])

            # Copy the sky callback wheel to ~/sky_utils in the home directory.
            candidate_config['setup'] = ('mkdir -p ~/sky_utils; '
                + f'cp "$(echo {backend_utils.SKY_REMOTE_PATH}/sky_callback-*.whl)" ~/sky_utils; '
                + candidate_config['setup'])
            candidate_configs.append(candidate_config)

        candidate_resources = _get_optimized_resources(candidate_configs)
        _print_candidate_resources(clusters, config, candidate_resources)
        return clusters, candidate_configs

    @staticmethod
    def launch(benchmark: str, clusters: List[str], candidate_configs: List[Dict[str, Any]], commandline_args: List[Dict[str, Any]]) -> None:
        # Use a Sky storage to save the benchmark logs.
        bucket_name, bucket_type = _get_benchmark_bucket()
        for candidate_config in candidate_configs:
            bucket_config = candidate_config['file_mounts'][_SKY_REMOTE_BENCHMARK_DIR]
            bucket_config['name'] = bucket_name
            bucket_config['store'] = bucket_type

        # Generate a temporary yaml file for each cluster.
        yaml_fds = []
        for cluster, candidate_config in zip(clusters, candidate_configs):
            f = tempfile.NamedTemporaryFile('w', prefix=f'{cluster}-', suffix='.yaml')
            backend_utils.dump_yaml(f.name, candidate_config)
            yaml_fds.append(f)

        # Generate a common launch command.
        cmd = ['-d', '-y']
        for arg_name, arg in commandline_args.items():
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
                    task_name = candidate_configs[0].get('name', None)
                    benchmark_state.add_benchmark(benchmark, task_name, bucket_name)
                    benchmark_created = True
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

        bucket_name = benchmark_state.get_benchmark_from_name(benchmark)['bucket']
        storage = data.Storage(bucket_name, source=None, persistent=True)
        bucket = list(storage.stores.values())[0]

        def _download_log(cluster: str):
            local_dir = os.path.join(_SKY_LOCAL_BENCHMARK_DIR, benchmark, cluster)
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
            local_dir = os.path.join(_SKY_LOCAL_BENCHMARK_DIR, benchmark, cluster)
            config_file = os.path.join(local_dir, 'config.json')
            with open(config_file, 'r') as f:
                config = json.load(f)
            start_ts = config['start_ts']
            total_steps = config.get('total_steps', None)

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
            sec_per_step = 0
            for i in range(num_steps):
                start = timestamps[2 * i]
                end = timestamps[2 * i + 1]
                sec_per_step += end - start
            sec_per_step /= num_steps

            record = benchmark_state.BenchmarkRecord(
                num_steps,
                sec_per_step,
                total_steps,
                start_ts,
                timestamps[0],
                timestamps[-1],
            )
            benchmark_state.update_benchmark_result(benchmark, cluster, record)

    @staticmethod
    def remove_logs(benchmark: str):
        log_dir = os.path.join(_SKY_LOCAL_BENCHMARK_DIR, benchmark)
        subprocess.run(['rm', '-rf', log_dir], check=False)
        # TODO: remove the logs in the bucket.
