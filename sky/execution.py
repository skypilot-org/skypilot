"""Execution layer: resource provisioner + task launcher.

Usage:

   >> sky.launch(planned_dag)

Current resource provisioners:

  - Ray autoscaler

Current task launcher:

  - ray exec + each task's commands
"""
import copy
import enum
import tempfile
import os
import subprocess
import textwrap
import time
from typing import Any, List, Optional

import colorama

import sky
from sky import backends
from sky import clouds
from sky import exceptions
from sky import global_user_state
from sky import optimizer
from sky import sky_logging
from sky import spot
from sky.backends import backend_utils
from sky.clouds import gcp
from sky.data import data_utils
from sky.data import storage
from sky.usage import usage_lib
from sky.utils import common_utils
from sky.utils import env_options, timeline
from sky.utils import subprocess_utils
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

OptimizeTarget = optimizer.OptimizeTarget
_MAX_SPOT_JOB_LENGTH = 10

# Message thrown when APIs sky.{exec,launch,spot_launch}() received a string
# instead of a Dag.  CLI (cli.py) is implemented by us so should not trigger
# this.
_ENTRYPOINT_STRING_AS_DAG_MESSAGE = """\
Expected a sky.Dag but received a string.

If you meant to run a command, make it a Task's run command and wrap as a Dag:

  def to_dag(command, gpu=None):
      with sky.Dag() as dag:
          sky.Task(run=command).set_resources(
              sky.Resources(accelerators=gpu))
      return dag

The command can then be run as:

  sky.exec(to_dag(cmd), cluster_name=..., ...)
  # Or use {'V100': 1}, 'V100:0.5', etc.
  sky.exec(to_dag(cmd, gpu='V100'), cluster_name=..., ...)

  sky.launch(to_dag(cmd), ...)

  sky.spot_launch(to_dag(cmd), ...)
""".strip()


def _type_check_dag(dag):
    """Raises TypeError if 'dag' is not a 'sky.Dag'."""
    # Not suppressing stacktrace: when calling this via API user may want to
    # see their own program in the stacktrace. Our CLI impl would not trigger
    # these errors.
    if isinstance(dag, str):
        raise TypeError(_ENTRYPOINT_STRING_AS_DAG_MESSAGE)
    elif not isinstance(dag, sky.Dag):
        raise TypeError('Expected a sky.Dag but received argument of type: '
                        f'{type(dag)}')


class Stage(enum.Enum):
    """Stages for a run of a sky.Task."""
    # TODO: rename actual methods to be consistent.
    OPTIMIZE = enum.auto()
    PROVISION = enum.auto()
    SYNC_WORKDIR = enum.auto()
    SYNC_FILE_MOUNTS = enum.auto()
    SETUP = enum.auto()
    PRE_EXEC = enum.auto()
    EXEC = enum.auto()
    TEARDOWN = enum.auto()


def _execute(
    dag: sky.Dag,
    dryrun: bool = False,
    teardown: bool = False,
    stream_logs: bool = True,
    handle: Any = None,
    backend: Optional[backends.Backend] = None,
    retry_until_up: bool = False,
    optimize_target: OptimizeTarget = OptimizeTarget.COST,
    stages: Optional[List[Stage]] = None,
    cluster_name: Optional[str] = None,
    detach_run: bool = False,
    idle_minutes_to_autostop: Optional[int] = None,
) -> None:
    """Runs a DAG.

    If the DAG has not been optimized yet, this will call sky.optimize() for
    the caller.

    Args:
      dag: sky.Dag.
      dryrun: bool; if True, only print the provision info (e.g., cluster
        yaml).
      teardown: bool; whether to teardown the launched resources after
        execution.
      stream_logs: bool; whether to stream all tasks' outputs to the client.
      handle: Any; if provided, execution will use an existing backend cluster
        handle instead of provisioning a new one.
      backend: Backend; backend to use for executing the tasks. Defaults to
        CloudVmRayBackend()
      retry_until_up: bool; whether to retry the provisioning until the cluster
        is up.
      optimize_target: OptimizeTarget; the dag optimization metric, e.g.
        OptimizeTarget.COST.
      stages: List of stages to run.  If None, run the whole life cycle of
        execution; otherwise, just the specified stages.  Used for `sky exec`
        skipping all setup steps.
      cluster_name: Name of the cluster to create/reuse.  If None,
        auto-generate a name.
      detach_run: bool; whether to detach the process after the job submitted.
      autostop_idle_minutes: int; if provided, the cluster will be set to
        autostop after this many minutes of idleness.
    """
    _type_check_dag(dag)
    assert len(dag) == 1, f'We support 1 task for now. {dag}'
    task = dag.tasks[0]

    if task.need_spot_recovery:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                'Spot recovery is specified in the task. To launch the '
                'managed spot job, please use: sky spot launch')

    cluster_exists = False
    if cluster_name is not None:
        existing_handle = global_user_state.get_handle_from_cluster_name(
            cluster_name)
        cluster_exists = existing_handle is not None

    backend = backend if backend is not None else backends.CloudVmRayBackend()
    if not isinstance(backend, backends.CloudVmRayBackend
                     ) and idle_minutes_to_autostop is not None:
        # TODO(zhwu): Autostop is not supported for non-CloudVmRayBackend.
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                f'Backend {backend.NAME} does not support autostop, please try '
                f'{backends.CloudVmRayBackend.NAME}')

    if not cluster_exists and (stages is None or Stage.OPTIMIZE in stages):
        if task.best_resources is None:
            # TODO: fix this for the situation where number of requested
            # accelerators is not an integer.
            if isinstance(backend, backends.CloudVmRayBackend):
                # TODO: adding this check because docker backend on a
                # no-credential machine should not enter optimize(), which
                # would directly error out ('No cloud is enabled...').  Fix by
                # moving `sky check` checks out of optimize()?
                dag = sky.optimize(dag, minimize=optimize_target)
                task = dag.tasks[0]  # Keep: dag may have been deep-copied.
                assert task.best_resources is not None, task

    backend.register_info(dag=dag, optimize_target=optimize_target)

    if task.storage_mounts is not None:
        # Optimizer should eventually choose where to store bucket
        task.add_storage_mounts()

    try:
        if stages is None or Stage.PROVISION in stages:
            if handle is None:
                handle = backend.provision(task,
                                           task.best_resources,
                                           dryrun=dryrun,
                                           stream_logs=stream_logs,
                                           cluster_name=cluster_name,
                                           retry_until_up=retry_until_up)

        if dryrun:
            logger.info('Dry run finished.')
            return

        if stages is None or Stage.SYNC_WORKDIR in stages:
            if task.workdir is not None:
                backend.sync_workdir(handle, task.workdir)

        if stages is None or Stage.SYNC_FILE_MOUNTS in stages:
            backend.sync_file_mounts(handle, task.file_mounts,
                                     task.storage_mounts)

        if stages is None or Stage.SETUP in stages:
            backend.setup(handle, task)

        if stages is None or Stage.PRE_EXEC in stages:
            if idle_minutes_to_autostop is not None:
                backend.set_autostop(handle, idle_minutes_to_autostop)

        if stages is None or Stage.EXEC in stages:
            try:
                global_user_state.update_last_use(handle.get_cluster_name())
                backend.execute(handle, task, detach_run)
            finally:
                # Enables post_execute() to be run after KeyboardInterrupt.
                backend.post_execute(handle, teardown)

        if stages is None or Stage.TEARDOWN in stages:
            if teardown:
                backend.teardown_ephemeral_storage(task)
                backend.teardown(handle, terminate=True)
    finally:
        # UX: print live clusters to make users aware (to save costs).
        # Needed because this finally doesn't always get executed on errors.
        # Disable the usage collection for this status command.
        env = dict(os.environ,
                   **{env_options.Options.DISABLE_LOGGING.value: '1'})
        if cluster_name == spot.SPOT_CONTROLLER_NAME:
            # For spot controller task, it requires a while to have the
            # managed spot status shown in the status table.
            time.sleep(0.5)
            subprocess_utils.run(
                f'sky spot status | head -n {_MAX_SPOT_JOB_LENGTH}')
        else:
            subprocess_utils.run('sky status', env=env)
        print()
        print('\x1b[?25h', end='')  # Show cursor.


def _launch_chain(dag: sky.Dag,
                  cluster_name: Optional[str] = None,
                  retry_until_up: bool = False,
                  dryrun: bool = False,
                  teardown: bool = False,
                  stream_logs: bool = True,
                  backend: Optional[backends.Backend] = None,
                  optimize_target: OptimizeTarget = OptimizeTarget.COST):
    """Launches a chain of tasks."""

    if cluster_name is None:
        cluster_name = backend_utils.generate_cluster_name()
    sky.optimize(dag, minimize=optimize_target)
    dag = copy.deepcopy(dag)
    tasks = list(dag.get_sorted_tasks())
    store_type = None
    output_store_path = None
    for i, task in enumerate(tasks):
        logger.info(f'==== Task {i+1} / {len(tasks)}: {task.name} ====')
        task_cluster_name = f'{cluster_name}-{i}-{task.name.replace("_", "-")}'
        input_store_path = task.get_inputs()

        inputs_outputs_on_bucket = task.inputs_outputs_on_bucket

        if input_store_path is not None:
            if input_store_path.startswith('CLOUD://'):
                assert store_type is not None, (i, task)
                input_store_path = input_store_path.replace(
                    'CLOUD://', store_type.value.lower() + '://')
                if output_store_path != input_store_path:
                    raise ValueError(
                        f'Ambiguous inputs {input_store_path} can only be '
                        'used when a previous task has the outputs with the '
                        f'same name {output_vm_path}.')

            if inputs_outputs_on_bucket:
                store_str = input_store_path.split('://')[0]
                if store_str == 's3':
                    # TODO(zhwu): if training on AWS, need fix.
                    input_vm_path = f'gs://{task_cluster_name}-inputs-{i}'
                    logger.info(f'transfer data from {input_store_path} to '
                                f'{input_vm_path}')
                    # Using the multi-region gs bucket, which will be free for
                    # data egressing to another GCP service within US.
                    # https://cloud.google.com/storage/pricing#multi-regions
                    # pylint: disable=line-too-long
                    subprocess.run(f'gsutil mb {input_vm_path} || true', shell=True, check=True)
                    if not dryrun:
                        input_storage_name = storage.get_storage_name_from_uri(input_store_path)
                        input_storage_name_gcs = storage.get_storage_name_from_uri(input_vm_path)
                        sky.data.data_transfer.s3_to_gcs(input_storage_name, input_storage_name_gcs)
                    # pylint: disable=pointless-string-statement
                    """ # pylint: disable=line-too-long

                    transfer_command = [
                        f'gsutil mb {input_vm_path} || true',
                        # f'gsutil -m rsync -r {input_store_path} {input_vm_path}',
                    ]

                    input_storage_name = storage.get_storage_name_from_uri(input_store_path)
                    input_storage_name_gcs = storage.get_storage_name_from_uri(input_vm_path)
                    sky.data.data_transfer.s3_to_gcs(input_storage_name, input_storage_name_gcs)
                    """
                elif store_str == 'gs':
                    input_vm_path = input_store_path
                else:
                    raise NotImplementedError
            else:
                input_vm_path = f'~/.sky/task-inputs-{i}'
                file_mounts_from_inputs = {input_vm_path: input_store_path}
                task.update_file_mounts(file_mounts_from_inputs)
            logger.info(f'Implied input path: {input_vm_path}')

            if task.setup is not None:
                task.setup = task.setup.replace('INPUTS[0]', input_vm_path)
            if task.run is not None:
                task.run = task.run.replace('INPUTS[0]', input_vm_path)

        task.set_resources(task.best_resources)

        cloud = task.best_resources.cloud

        output_store_path = copy.copy(task.get_outputs())
        if output_store_path is not None:
            output_storage_name = storage.get_storage_name_from_uri(
                output_store_path)
            if inputs_outputs_on_bucket:
                output_vm_path = f'gs://{output_storage_name}'
                # Use multi-region bucket by not specifying the location
                create_bucket_cmd = (f'gsutil mb {output_vm_path} || true')
                subprocess.run(create_bucket_cmd, shell=True, check=True)
            else:
                output_vm_path = f'~/.sky/task-outputs-{i}'

            if task.setup is not None:
                task.setup = task.setup.replace('OUTPUTS[0]', output_vm_path)
            if task.run is not None:
                task.run = task.run.replace('OUTPUTS[0]', output_vm_path)

            if not output_store_path.startswith('CLOUD://'):
                store_type = storage.StoreType(
                    output_store_path.partition('://')[0])
            elif isinstance(cloud, clouds.Azure):
                # If the cloud is Azure, send to the bucket on the next cloud,
                # as object store is not supported for Azure in our code yet.
                next_cloud = tasks[i + 1].best_resources.cloud
                store_type = storage.StoreType.from_cloud(next_cloud)
            else:
                store_type = storage.StoreType.from_cloud(cloud)

            output_store_path = f'{store_type.value.lower()}://{output_storage_name}'
            logger.info(f'Implied output path: {output_store_path}')

            # Upload current outputs to the cloud storage of current cloud
            if not inputs_outputs_on_bucket:
                sky_storage_codegen = (
                    'import sky; storage = sky.Storage('
                    f'name={output_storage_name!r},'
                    f' source={output_vm_path!r}); storage.add_store('
                    f'sky.StoreType({store_type.value!r}));')

                upload_code_gen = textwrap.dedent(f"""\
                    echo Uploading the outputs to the bucket
                    conda deactivate
                    {clouds.gcp.GCLOUD_INSTALLATION_COMMAND}
                    pip install boto3 awscli pycryptodome==3.12.0 google-api-python-client google-cloud-storage 2>&1 > /dev/null
                    python -u -c {sky_storage_codegen!r}
                    echo Uploaded.
                    """)
                task.run = task.run + '\n' + upload_code_gen

                if task.setup is None:
                    task.setup = ''
                task.setup += '\n' + f'mkdir -p {output_vm_path}'
        # print(task.to_yaml_config())
        with sky.Dag() as task_dag:
            task_dag.add(task)

        launch(task_dag,
               cluster_name=task_cluster_name,
               stream_logs=stream_logs,
               backend=backend,
               dryrun=dryrun,
               retry_until_up=retry_until_up)

        if not dryrun and teardown:
            logger.info(f'Terminate {task_cluster_name}.')
            with subprocess.Popen(f'sky down -y {task_cluster_name}',
                                  shell=True) as proc:
                if i == len(tasks) - 1:
                    proc.wait()


@timeline.event
@usage_lib.entrypoint
def launch(
    dag: sky.Dag,
    cluster_name: Optional[str] = None,
    retry_until_up: bool = False,
    idle_minutes_to_autostop: Optional[int] = None,
    dryrun: bool = False,
    teardown: bool = False,
    stream_logs: bool = True,
    backend: Optional[backends.Backend] = None,
    optimize_target: OptimizeTarget = OptimizeTarget.COST,
    detach_run: bool = False,
):
    """Launch a sky.DAG (rerun setup if cluster exists).

    Args:
        dag: sky.DAG to launch.
        cluster_name: name of the cluster to create/reuse.  If None,
            auto-generate a name.
        retry_until_up: whether to retry launching the cluster until it is
            up.
        idle_minutes_to_autostop: if provided, the cluster will be auto-stop
            after this many minutes of idleness.

    Examples:
        >>> import sky
        >>> with sky.Dag() as dag:
        >>>     task = sky.Task(run='echo hello SkyPilot')
        >>>     task.set_resources(
        ...             sky.Resources(
        ...                 cloud=sky.AWS(),
        ...                 accelerators='V100:4'
        ...             )
        ...     )
        >>> sky.launch(dag, cluster_name='my-cluster')

    """
    backend_utils.check_cluster_name_not_reserved(cluster_name,
                                                  operation_str='sky.launch')
    if not dag.is_chain():
        raise ValueError('dag must be a chain')

    if len(dag.tasks) != 1:
        logger.warning(
            '`launch_chain` is still experimental and subject to change in the '
            'future. Dag should be a chain of tasks.')
        _launch_chain(dag=dag,
                      cluster_name=cluster_name,
                      retry_until_up=retry_until_up,
                      dryrun=dryrun,
                      teardown=teardown,
                      stream_logs=stream_logs,
                      backend=backend,
                      optimize_target=optimize_target)
        return

    _execute(
        dag=dag,
        dryrun=dryrun,
        teardown=teardown,
        stream_logs=stream_logs,
        handle=None,
        backend=backend,
        retry_until_up=retry_until_up,
        optimize_target=optimize_target,
        cluster_name=cluster_name,
        detach_run=detach_run,
        idle_minutes_to_autostop=idle_minutes_to_autostop,
    )


@usage_lib.entrypoint
def exec(  # pylint: disable=redefined-builtin
    dag: sky.Dag,
    cluster_name: str,
    dryrun: bool = False,
    teardown: bool = False,
    stream_logs: bool = True,
    backend: Optional[backends.Backend] = None,
    optimize_target: OptimizeTarget = OptimizeTarget.COST,
    detach_run: bool = False,
):
    backend_utils.check_cluster_name_not_reserved(cluster_name,
                                                  operation_str='sky.exec')

    status, handle = backend_utils.refresh_cluster_status_handle(cluster_name)
    with ux_utils.print_exception_no_traceback():
        if handle is None:
            raise ValueError(f'Cluster {cluster_name!r} not found.  '
                             'Use `sky launch` to provision first.')
        if status != global_user_state.ClusterStatus.UP:
            raise ValueError(f'Cluster {cluster_name!r} is not up.  '
                             'Use `sky status` to check the status.')
    _execute(dag=dag,
             dryrun=dryrun,
             teardown=teardown,
             stream_logs=stream_logs,
             handle=handle,
             backend=backend,
             optimize_target=optimize_target,
             stages=[
                 Stage.SYNC_WORKDIR,
                 Stage.EXEC,
             ],
             cluster_name=cluster_name,
             detach_run=detach_run)


def spot_launch(
    dag: sky.Dag,
    name: Optional[str] = None,
    stream_logs: bool = True,
    detach_run: bool = False,
    retry_until_up: bool = False,
):
    """Launch a managed spot job.

    Please refer to the sky.cli.spot_launch for the document.

    Args:
        dag: DAG to launch.
        name: Name of the spot job.
        detach_run: Whether to detach the run.

    Raises:
        ValueError: cluster does not exist.
        sky.exceptions.NotSupportedError: the feature is not supported.
    """
    if name is None:
        name = backend_utils.generate_cluster_name()

    _type_check_dag(dag)
    assert len(dag.tasks) == 1, ('Only one task is allowed in a spot launch.',
                                 dag)
    task = dag.tasks[0]
    assert len(task.resources) == 1, task
    resources = list(task.resources)[0]

    change_default_value = dict()
    if not resources.use_spot_specified:
        logger.info('Field use_spot not specified; defaulting to True.')
        change_default_value['use_spot'] = True
    if resources.spot_recovery is None:
        logger.info('No spot recovery strategy specified; defaulting to '
                    f'{spot.SPOT_DEFAULT_STRATEGY}.')
        change_default_value['spot_recovery'] = spot.SPOT_DEFAULT_STRATEGY

    new_resources = resources.copy(**change_default_value)
    task.set_resources({new_resources})

    if task.run is None:
        print(f'{colorama.Fore.GREEN}'
              'Skipping the managed spot task as the run section is not set.'
              f'{colorama.Style.RESET_ALL}')
        return

    # TODO(zhwu): Refactor the Task (as Resources), so that we can enforce the
    # following validations.
    # Check the file mounts in the task.
    # Disallow all local file mounts (copy mounts).
    if task.workdir is not None:
        with ux_utils.print_exception_no_traceback():
            raise exceptions.NotSupportedError(
                'Workdir is currently not allowed for managed spot jobs.\n'
                'Hint: use Storage to auto-upload the workdir to a cloud '
                'bucket.')
    copy_mounts = task.get_local_to_remote_file_mounts()
    if copy_mounts:
        local_sources = '\t' + '\n\t'.join(
            src for _, src in copy_mounts.items())
        with ux_utils.print_exception_no_traceback():
            raise exceptions.NotSupportedError(
                'Local file mounts are currently not allowed for managed spot '
                f'jobs.\nFound local source paths:\n{local_sources}\nHint: use '
                'Storage to auto-upload local files to a cloud bucket.')

    # Copy the local source to a bucket. The task will not be executed locally,
    # so we need to copy the files to the bucket manually here before sending to
    # the remote spot controller.
    with backend_utils.suppress_output():
        task.add_storage_mounts()

    # Replace the source field that is local path in all storage_mounts with
    # bucket URI and remove the name field.
    for storage_obj in task.storage_mounts.values():
        if (storage_obj.source is not None and
                not data_utils.is_cloud_store_url(storage_obj.source)):
            # Need to replace the local path with bucket URI, and remove the
            # name field, so that the storage mount can work on the spot
            # controller.
            store_types = list(storage_obj.stores.keys())
            assert len(store_types) == 1, (
                'We only support one store type for now.', storage_obj.stores)
            store_type = store_types[0]
            if store_type == storage.StoreType.S3:
                storage_obj.source = f's3://{storage_obj.name}'
            elif store_type == storage.StoreType.GCS:
                storage_obj.source = f'gs://{storage_obj.name}'
            else:
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.NotSupportedError(
                        f'Unsupported store type: {store_type}')
            storage_obj.name = None
            storage_obj.force_delete = True

    with tempfile.NamedTemporaryFile(prefix=f'spot-task-{name}-',
                                     mode='w') as f:
        task_config = task.to_yaml_config()
        common_utils.dump_yaml(f.name, task_config)

        controller_name = spot.SPOT_CONTROLLER_NAME
        yaml_path = backend_utils.fill_template(
            spot.SPOT_CONTROLLER_TEMPLATE, {
                'remote_user_yaml_prefix': spot.SPOT_TASK_YAML_PREFIX,
                'user_yaml_path': f.name,
                'spot_controller': controller_name,
                'cluster_name': name,
                'gcloud_installation_commands': gcp.GCLOUD_INSTALLATION_COMMAND,
                'is_dev': env_options.Options.IS_DEVELOPER.get(),
                'disable_logging': env_options.Options.DISABLE_LOGGING.get(),
                'logging_user_hash': common_utils.get_user_hash(),
                'retry_until_up': retry_until_up,
            },
            output_prefix=spot.SPOT_CONTROLLER_YAML_PREFIX)
        with sky.Dag() as spot_dag:
            controller_task = sky.Task.from_yaml(yaml_path)
            controller_task.spot_task = task
            assert len(controller_task.resources) == 1
        print(f'{colorama.Fore.YELLOW}'
              f'Launching managed spot job {name} from spot controller...'
              f'{colorama.Style.RESET_ALL}')
        print('Launching spot controller...')
        _execute(
            dag=spot_dag,
            stream_logs=stream_logs,
            cluster_name=controller_name,
            detach_run=detach_run,
            idle_minutes_to_autostop=spot.
            SPOT_CONTROLLER_IDLE_MINUTES_TO_AUTOSTOP,
            retry_until_up=True,
        )
