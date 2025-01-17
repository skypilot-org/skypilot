"""Benchmark utils: Utility functions to manage benchmarking process."""
import copy
import getpass
import glob
import json
from multiprocessing import pool
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import typing
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
import uuid

import colorama
import prettytable
from rich import progress as rich_progress

import sky
from sky import backends
from sky import clouds
from sky import data
from sky import global_user_state
from sky import sky_logging
from sky import status_lib
from sky.backends import backend_utils
from sky.benchmark import benchmark_state
from sky.data import storage as storage_lib
from sky.skylet import constants
from sky.skylet import job_lib
from sky.skylet import log_lib
from sky.utils import common_utils
from sky.utils import log_utils
from sky.utils import rich_utils
from sky.utils import subprocess_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib

logger = sky_logging.init_logger(__name__)

_SKY_LOCAL_BENCHMARK_DIR = os.path.expanduser('~/.sky/benchmarks')
_SKY_REMOTE_BENCHMARK_DIR = '~/.sky/sky_benchmark_dir'
# NOTE: This must be the same as _SKY_REMOTE_BENCHMARK_DIR
# in sky/callbacks/sky_callback/base.py.
_SKY_REMOTE_BENCHMARK_DIR_SYMLINK = '~/sky_benchmark_dir'

# NOTE: This must be the same as _BENCHMARK_SUMMARY
# in sky/callbacks/sky_callback/base.py.
_BENCHMARK_SUMMARY = 'summary.json'
_RUN_START = 'run_start.txt'
_RUN_END = 'run_end.txt'

_Config = Dict[str, Any]


def _generate_cluster_names(benchmark: str, num_clusters: int) -> List[str]:
    if num_clusters == 1:
        names = [f'sky-bench-{benchmark}']
    else:
        names = [f'sky-bench-{benchmark}-{i}' for i in range(num_clusters)]
    for name in names:
        if global_user_state.get_cluster_from_name(name) is not None:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Cluster name {name} is taken. '
                                 'Try using a different benchmark name.')
    return names


def _make_script_with_timelogs(script: str, start_path: str,
                               end_path: str) -> str:
    """Add prologue and epilogue that log the start and end times of the script.

    Using the logs, we can get the job status and duration even when the cluster
    has stopped or terminated. Note that the end time is only logged when the
    command finishes successfully.
    """
    return textwrap.dedent(f"""\
        echo $(date +%s.%N) > {start_path}
        {script}
        EXIT_CODE=$?
        if [ $EXIT_CODE -eq 0 ]; then
            echo $(date +%s.%N) > {end_path}
        fi
        exit $EXIT_CODE
        """)


def _get_optimized_resources(
        candidate_configs: List[_Config]) -> List['resources_lib.Resources']:
    candidate_configs = copy.deepcopy(candidate_configs)
    optimized_resources = []
    for config in candidate_configs:
        with sky.Dag() as dag:
            resources = config.get('resources', None)
            resources = sky.Resources.from_yaml_config(resources)
            task = sky.Task()
            task.set_resources(resources)

        dag = sky.optimize(dag, quiet=True)
        task = dag.tasks[0]
        optimized_resources.append(task.best_resources)
    return optimized_resources


def _print_candidate_resources(
        benchmark: str, clusters: List[str], config: _Config,
        candidate_resources: List['resources_lib.Resources']) -> None:
    task_str = config.get('name', 'a task')
    num_nodes = config.get('num_nodes', 1)
    logger.info(f'{colorama.Style.BRIGHT}Benchmarking {task_str} '
                f'on candidate resources (benchmark name: {benchmark}):'
                f'{colorama.Style.RESET_ALL}')

    columns = [
        'CLUSTER',
        'CLOUD',
        '# NODES',
        'INSTANCE',
        'vCPUs',
        'Mem(GB)',
        'ACCELERATORS',
        'PRICE ($/hr)',
    ]
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
        cloud = resources.cloud
        vcpus, mem = cloud.get_vcpus_mem_from_instance_type(
            resources.instance_type)

        def format_number(x):
            if x is None:
                return '-'
            elif x.is_integer():
                return str(int(x))
            else:
                return f'{x:.1f}'

        vcpus = format_number(vcpus)
        mem = format_number(mem)
        cost = num_nodes * resources.get_cost(3600)
        spot = '[Spot]' if resources.use_spot else ''
        row = [
            cluster, cloud, num_nodes, resources.instance_type + spot, vcpus,
            mem, accelerators, f'{cost:.2f}'
        ]
        candidate_table.add_row(row)
    logger.info(f'{candidate_table}\n')


def _create_benchmark_bucket() -> Tuple[str, str]:
    # Generate a bucket name.
    # TODO(woosuk): Use a more pleasant naming scheme.
    # TODO(woosuk): Ensure that the bucket name is globally unique.
    bucket_name = f'sky-bench-{uuid.uuid4().hex[:4]}-{getpass.getuser()}'

    # Select the bucket type.
    enabled_clouds = storage_lib.get_cached_enabled_storage_clouds_or_refresh(
        raise_if_no_cloud_access=True)
    # Sky Benchmark only supports S3 (see _download_remote_dir and
    # _delete_remote_dir).
    enabled_clouds = [
        cloud for cloud in enabled_clouds if cloud in [str(clouds.AWS())]
    ]
    assert enabled_clouds, ('No enabled cloud storage found. Sky Benchmark '
                            'requires GCP or AWS to store logs.')
    bucket_type = data.StoreType.from_cloud(enabled_clouds[0]).value

    # Create a benchmark bucket.
    logger.info(f'Creating a bucket {bucket_name} to save the benchmark logs.')
    storage = data.Storage(bucket_name, source=None, persistent=True)
    storage.add_store(bucket_type)

    # Save the bucket name and type to the config.
    benchmark_state.set_benchmark_bucket(bucket_name, bucket_type)
    return bucket_name, bucket_type


def _format_err_msg(msg: str):
    return f'{colorama.Fore.RED}{msg}{colorama.Style.RESET_ALL}'


def _parallel_run_with_interrupt_handling(func: Callable,
                                          args: List[Any]) -> List[Any]:
    with pool.ThreadPool(processes=len(args)) as p:
        try:
            return list(p.imap(func, args))
        except KeyboardInterrupt:
            print()
            logger.error(_format_err_msg('Interrupted by user.'))
            subprocess_utils.run('sky status')
            sys.exit(1)


def _launch_with_log_suppress_exception(
        cluster: str, cmd: List[str],
        log_dir: str) -> Union[Tuple[int, str], Exception]:
    """Executes `sky launch` in a subprocess and returns normally.

    This function does not propagate any error so that failures in a
    launch thread do not disrupt the other parallel launch threads.
    """
    prefix_color = colorama.Fore.MAGENTA
    prefix = f'{prefix_color}({cluster}){colorama.Style.RESET_ALL} '
    try:
        returncode, _, stderr = log_lib.run_with_log(
            cmd,
            log_path=os.path.join(log_dir, f'{cluster}.log'),
            stream_logs=True,
            streaming_prefix=prefix,
            start_streaming_at='Creating a new cluster: ',
            skip_lines=[
                'Tip: to reuse an existing cluster, specify --cluster (-c).',
            ],
            end_streaming_at='Job submitted with Job ID: ',
            require_outputs=True,
        )
        # Report any error from the `sky launch` subprocess.
        return returncode, stderr
    except Exception as e:  # pylint: disable=broad-except
        # FIXME(woosuk): Avoid using general Exception.
        # Report any error in executing and processing the outputs of
        # the `sky launch` subprocess.
        return e


def _download_remote_dir(remote_dir: str, local_dir: str,
                         bucket_type: data.StoreType) -> None:
    # FIXME(woosuk): Replace this function with bucket.download_remote_dir.
    if bucket_type == data.StoreType.S3:
        remote_dir = f's3://{remote_dir}'
        subprocess.run(
            ['aws', 's3', 'cp', '--recursive', remote_dir, local_dir],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True)
    else:
        raise RuntimeError(f'{bucket_type} is not supported yet.')


def _delete_remote_dir(remote_dir: str, bucket_type: data.StoreType) -> None:
    # FIXME(woosuk): Replace this function with bucket.delete_remote_dir.
    if bucket_type == data.StoreType.S3:
        remote_dir = f's3://{remote_dir}'
        subprocess.run(['aws', 's3', 'rm', '--recursive', remote_dir],
                       stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL,
                       check=True)
    else:
        raise RuntimeError(f'{bucket_type} is not supported yet.')


def _read_timestamp(path: str) -> float:
    with open(path, 'r', encoding='utf-8') as f:
        timestamp = f.readlines()
    assert len(timestamp) == 1
    return float(timestamp[0].strip())


def _update_benchmark_result(benchmark_result: Dict[str, Any]) -> Optional[str]:
    benchmark = benchmark_result['benchmark']
    benchmark_status = benchmark_result['status']
    cluster = benchmark_result['cluster']
    if benchmark_status.is_terminal():
        # No need to update.
        return

    # Get the start and end timestamps if exist.
    local_dir = os.path.join(_SKY_LOCAL_BENCHMARK_DIR, benchmark, cluster)
    run_start_path = os.path.join(local_dir, _RUN_START)
    start_time = None
    if os.path.exists(run_start_path):
        start_time = _read_timestamp(run_start_path)
    run_end_path = os.path.join(local_dir, _RUN_END)
    end_time = None
    if os.path.exists(run_end_path):
        # The job has terminated with a zero exit code. See
        # generate_benchmark_configs() which ensures the 'run' commands write
        # out end_time remotely on success; and the caller of this func which
        # downloads all benchmark log files including the end_time file to
        # local.
        end_time = _read_timestamp(run_end_path)

    # Get the status of the benchmarking cluster and job.
    record = global_user_state.get_cluster_from_name(cluster)
    cluster_status = None
    job_status = None
    if record is not None:
        cluster_status, handle = backend_utils.refresh_cluster_status_handle(
            cluster)
        if handle is not None:
            backend = backend_utils.get_backend_from_handle(handle)
            assert isinstance(backend, backends.CloudVmRayBackend)

            if cluster_status == status_lib.ClusterStatus.UP:
                # NOTE: The id of the benchmarking job must be 1.
                # TODO(woosuk): Handle exceptions.
                job_status = backend.get_job_status(handle,
                                                    job_ids=[1],
                                                    stream_logs=False)[1]

    logger.debug(f'Cluster {cluster}, cluster_status: {cluster_status}, '
                 f'benchmark_status {benchmark_status}, job_status: '
                 f'{job_status}, start_time {start_time}, end_time {end_time}')

    # Update the benchmark status.
    if end_time is not None:
        # The job has terminated with zero exit code.
        benchmark_status = benchmark_state.BenchmarkStatus.FINISHED
    elif cluster_status is None:
        # Candidate cluster: preempted or never successfully launched.
        #
        # Note that benchmark record is only inserted after all clusters
        # finished launch() (successful or not). See
        # launch_benchmark_clusters(). So this case doesn't include "just before
        # candidate cluster's launch() is called".

        # See above: if cluster_status is not UP, job_status is defined as None.
        assert job_status is None, job_status
        benchmark_status = benchmark_state.BenchmarkStatus.TERMINATED
    elif cluster_status == status_lib.ClusterStatus.INIT:
        # Candidate cluster's launch has something gone wrong, or is still
        # launching.

        # See above: if cluster_status is not UP, job_status is defined as None.
        assert job_status is None, job_status
        benchmark_status = benchmark_state.BenchmarkStatus.INIT
    elif cluster_status == status_lib.ClusterStatus.STOPPED:
        # Candidate cluster is auto-stopped, or user manually stops it at any
        # time. Also, end_time is None.

        # See above: if cluster_status is not UP, job_status is defined as None.
        assert job_status is None, job_status
        benchmark_status = benchmark_state.BenchmarkStatus.TERMINATED
    else:
        assert cluster_status == status_lib.ClusterStatus.UP, (
            'ClusterStatus enum should have been handled')
        if job_status is None:
            benchmark_status = benchmark_state.BenchmarkStatus.INIT
        else:
            if job_status < job_lib.JobStatus.RUNNING:
                benchmark_status = benchmark_state.BenchmarkStatus.INIT
            elif job_status == job_lib.JobStatus.RUNNING:
                benchmark_status = benchmark_state.BenchmarkStatus.RUNNING
            else:
                assert job_status.is_terminal(), '> RUNNING means terminal'
                # Case: cluster_status UP, job_status.is_terminal()
                if job_status == job_lib.JobStatus.SUCCEEDED:
                    # Since we download the benchmark logs before checking the
                    # cluster status, there is a chance that the end timestamp
                    # is saved and the cluster is stopped AFTER we download the
                    # logs.  In this case, we consider the current timestamp as
                    # the end time.
                    end_time = time.time()
                    benchmark_status = benchmark_state.BenchmarkStatus.FINISHED
                else:
                    benchmark_status = (
                        benchmark_state.BenchmarkStatus.TERMINATED)

    callback_log_dirs = glob.glob(os.path.join(local_dir, 'sky-callback-*'))
    if callback_log_dirs:
        # There can be multiple logs if the cluster has executed multiple jobs.
        # Here, we consider the first log as the log of the benchmarking job.
        log_dir = sorted(callback_log_dirs)[0]
        summary_path = os.path.join(log_dir, _BENCHMARK_SUMMARY)
    else:
        summary_path = None

    message = None
    if summary_path is not None and os.path.exists(summary_path):
        # (1) SkyCallback has saved the summary.
        with open(summary_path, 'r', encoding='utf-8') as f:
            summary = json.load(f)
        if end_time is None:
            last_time = summary['last_step_time']
        else:
            last_time = end_time
        if last_time is None:
            if job_status == job_lib.JobStatus.RUNNING:
                last_time = time.time()
            else:
                message = (f'No duration information found for {cluster}. '
                           'Check if at least 1 step has finished.')
        record = benchmark_state.BenchmarkRecord(
            start_time=start_time,
            last_time=last_time,
            num_steps_so_far=summary['num_steps'],
            seconds_per_step=summary['time_per_step'],
            estimated_total_seconds=summary['estimated_total_time'],
        )
    elif end_time is not None:
        # (2) The benchmarking job has terminated normally
        # without SkyCallback logs.
        record = benchmark_state.BenchmarkRecord(start_time=start_time,
                                                 last_time=end_time)
    elif job_status == job_lib.JobStatus.RUNNING:
        # (3) SkyCallback is not initialized yet or not used.
        message = ('SkyCallback is not initialized yet '
                   f'or not used for {cluster}.')
        record = benchmark_state.BenchmarkRecord(start_time=start_time,
                                                 last_time=time.time())
    elif benchmark_status == benchmark_state.BenchmarkStatus.TERMINATED:
        # (4) The benchmarking job has terminated abnormally.
        message = (f'The benchmarking job on {cluster} has terminated with '
                   'non-zero exit code.')
        record = benchmark_state.BenchmarkRecord(start_time=start_time,
                                                 last_time=None)
    else:
        # (5) Otherwise (e.g., cluster_status is INIT).
        message = f'No benchmark logs found for {cluster}.'
        record = benchmark_state.BenchmarkRecord(start_time=None,
                                                 last_time=None)
    benchmark_state.update_benchmark_result(benchmark, cluster,
                                            benchmark_status, record)
    return message


def generate_benchmark_configs(
    benchmark: str,
    config: _Config,
    candidates: List[Dict[str, str]],
) -> Tuple[List[str], List[_Config]]:
    # Generate a config for each cluster.
    clusters = _generate_cluster_names(benchmark, len(candidates))
    candidate_configs = []
    # TODO(woosuk): Use a jinja template.
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
            'name': None,
            'mode': 'MOUNT',
            'store': None,
        }

        benchmark_dir = os.path.join(_SKY_REMOTE_BENCHMARK_DIR, benchmark,
                                     cluster)
        if 'setup' not in candidate_config:
            candidate_config['setup'] = ''
        # Create a symbolic link to a directory in the benchmark bucket.
        candidate_config['setup'] = textwrap.dedent(f"""\
            mkdir -p {benchmark_dir}
            ln -s {benchmark_dir} {_SKY_REMOTE_BENCHMARK_DIR_SYMLINK}
            {candidate_config['setup']}""")

        # Log the start and end time of the benchmarking task.
        if 'run' not in candidate_config:
            candidate_config['run'] = ''
        candidate_config['run'] = _make_script_with_timelogs(
            candidate_config['run'], os.path.join(benchmark_dir, _RUN_START),
            os.path.join(benchmark_dir, _RUN_END))

        candidate_configs.append(candidate_config)
    return clusters, candidate_configs


def print_benchmark_clusters(benchmark: str, clusters: List[str],
                             config: _Config,
                             candidate_configs: List[_Config]) -> None:
    candidate_resources = _get_optimized_resources(candidate_configs)
    _print_candidate_resources(benchmark, clusters, config, candidate_resources)


def launch_benchmark_clusters(benchmark: str, clusters: List[str],
                              candidate_configs: List[_Config],
                              commandline_args: List[Dict[str, Any]]) -> bool:
    # Use a Sky storage to save the benchmark logs.
    bucket_name, bucket_type = benchmark_state.get_benchmark_bucket()
    if bucket_name is not None:
        handle = global_user_state.get_handle_from_storage_name(bucket_name)
        if handle is not None:
            assert bucket_type is not None

    # If the bucket does not exist, create one.
    if bucket_name is None or handle is None:
        bucket_name, bucket_type = _create_benchmark_bucket()

    # Remove the previous benchmark logs if exist.
    remove_benchmark_logs(benchmark, bucket_name, data.StoreType[bucket_type])

    # The benchmark bucket is mounted to _SKY_REMOTE_BENCHMARK_DIR.
    for candidate_config in candidate_configs:
        bucket_config = candidate_config['file_mounts'][
            _SKY_REMOTE_BENCHMARK_DIR]
        bucket_config['name'] = bucket_name
        bucket_config['store'] = bucket_type

    # Generate a temporary yaml file for each cluster.
    yaml_fds = []
    for cluster, candidate_config in zip(clusters, candidate_configs):
        # pylint: disable=consider-using-with
        f = tempfile.NamedTemporaryFile('w',
                                        prefix=f'{cluster}-',
                                        suffix='.yaml')
        common_utils.dump_yaml(f.name, candidate_config)
        yaml_fds.append(f)
        logger.debug(f'Generated temporary yaml file: {f.name}')

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
    launch_cmds = [['sky', 'launch', yaml_fd.name, '-c', cluster] + cmd
                   for yaml_fd, cluster in zip(yaml_fds, clusters)]

    # Save stdout/stderr from cluster launches.
    run_timestamp = sky_logging.get_run_timestamp()
    log_dir = os.path.join(constants.SKY_LOGS_DIRECTORY, run_timestamp)
    log_dir = os.path.expanduser(log_dir)
    logger.info(
        f'{colorama.Fore.YELLOW}To view stdout/stderr from individual '
        f'cluster launches, check the logs in{colorama.Style.RESET_ALL} '
        f'{colorama.Style.BRIGHT}{log_dir}/{colorama.Style.RESET_ALL}')

    # Launch the benchmarking clusters in parallel.
    outputs = _parallel_run_with_interrupt_handling(
        lambda args: _launch_with_log_suppress_exception(*args, log_dir=log_dir
                                                        ),
        list(zip(clusters, launch_cmds)))

    # Handle the errors raised during the cluster launch.
    for cluster, output in zip(clusters, outputs):
        if isinstance(output, Exception):
            logger.error(_format_err_msg(f'Launching {cluster} failed.'))
            logger.error(output)
        else:
            returncode, stderr = output
            if returncode != 0:
                message = _format_err_msg(
                    f'Launching {cluster} failed with code {returncode}.')
                logger.error(message)
                logger.error(stderr)

    # Delete the temporary yaml files.
    for f in yaml_fds:
        f.close()

    # If at least one cluster has been provisioned (in whatever state),
    # add the benchmark to the state so that `sky bench down` can
    # terminate the launched clusters.
    benchmark_created = False
    for cluster in clusters:
        record = global_user_state.get_cluster_from_name(cluster)
        if record is not None:
            if not benchmark_created:
                task_name = candidate_configs[0].get('name', None)
                benchmark_state.add_benchmark(benchmark, task_name, bucket_name)
                benchmark_created = True
            benchmark_state.add_benchmark_result(benchmark, record['handle'])
    return benchmark_created


def update_benchmark_state(benchmark: str) -> None:
    benchmark_results = benchmark_state.get_benchmark_results(benchmark)
    if all(result['status'].is_terminal() for result in benchmark_results):
        return

    bucket_name = benchmark_state.get_benchmark_from_name(benchmark)['bucket']
    handle = global_user_state.get_handle_from_storage_name(bucket_name)
    bucket_type = list(handle.sky_stores.keys())[0]

    # Download the benchmark logs from the benchmark bucket.
    # FIXME(woosuk): Do not download the logs if not necessary.
    remote_dir = os.path.join(bucket_name, benchmark)
    local_dir = os.path.join(_SKY_LOCAL_BENCHMARK_DIR, benchmark)
    os.makedirs(local_dir, exist_ok=True)
    with rich_utils.safe_status(
            ux_utils.spinner_message('Downloading benchmark logs')):
        _download_remote_dir(remote_dir, local_dir, bucket_type)

    # Update the benchmark results in parallel.
    num_candidates = len(benchmark_results)
    plural = 's' if num_candidates > 1 else ''
    progress = rich_progress.Progress(transient=True,
                                      redirect_stdout=False,
                                      redirect_stderr=False)
    task = progress.add_task(ux_utils.spinner_message(
        f'Processing {num_candidates} benchmark result{plural}'),
                             total=num_candidates)

    def _update_with_progress_bar(arg: Any) -> None:
        message = _update_benchmark_result(arg)
        if message is None:
            progress.update(task, advance=1)
        else:
            progress.stop()
            logger.info(
                f'{colorama.Fore.YELLOW}{message}{colorama.Style.RESET_ALL}')
            progress.start()

    with progress:
        _parallel_run_with_interrupt_handling(_update_with_progress_bar,
                                              benchmark_results)
        progress.live.transient = False
        progress.refresh()


def remove_benchmark_logs(benchmark: str, bucket_name: str,
                          bucket_type: data.StoreType) -> None:
    # Delete logs in the benchmark bucket.
    remote_dir = os.path.join(bucket_name, benchmark)
    _delete_remote_dir(remote_dir, bucket_type)
    # Delete logs in the local storage.
    local_dir = os.path.join(_SKY_LOCAL_BENCHMARK_DIR, benchmark)
    subprocess.run(['rm', '-rf', local_dir], check=False)
