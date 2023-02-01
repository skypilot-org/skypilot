"""Execution layer: resource provisioner + task launcher.

Usage:

   >> sky.launch(planned_dag)

Current resource privisioners:

  - Ray autoscaler

Current task launcher:

  - ray exec + each task's commands
"""
import copy
import enum
import getpass
import tempfile
import os
from typing import Any, List, Optional, Union

import colorama

import sky
from sky import backends
from sky import clouds
from sky import exceptions
from sky import global_user_state
from sky import optimizer
from sky import skypilot_config
from sky import sky_logging
from sky import spot
from sky import task as task_lib
from sky.backends import backend_utils
from sky.clouds import gcp
from sky.data import data_utils
from sky.data import storage as storage_lib
from sky.usage import usage_lib
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import env_options, timeline
from sky.utils import subprocess_utils
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

OptimizeTarget = optimizer.OptimizeTarget

# Message thrown when APIs sky.{exec,launch,spot_launch}() received a string
# instead of a Dag.  CLI (cli.py) is implemented by us so should not trigger
# this.
_ENTRYPOINT_STRING_AS_DAG_MESSAGE = """\
Expected a sky.Task or sky.Dag but received a string.

If you meant to run a command, make it a Task's run command:

    task = sky.Task(run=command)

The command can then be run as:

  sky.exec(task, cluster_name=..., ...)
  # Or use {'V100': 1}, 'V100:0.5', etc.
  task.set_resources(sky.Resources(accelerators='V100:1'))
  sky.exec(task, cluster_name=..., ...)

  sky.launch(task, ...)

  sky.spot_launch(task, ...)
""".strip()


def _convert_to_dag(entrypoint: Any) -> 'sky.Dag':
    """Convert the entrypoint to a sky.Dag.

    Raises TypeError if 'entrypoint' is not a 'sky.Task' or 'sky.Dag'.
    """
    # Not suppressing stacktrace: when calling this via API user may want to
    # see their own program in the stacktrace. Our CLI impl would not trigger
    # these errors.
    if isinstance(entrypoint, str):
        raise TypeError(_ENTRYPOINT_STRING_AS_DAG_MESSAGE)
    elif isinstance(entrypoint, sky.Dag):
        return copy.deepcopy(entrypoint)
    elif isinstance(entrypoint, task_lib.Task):
        entrypoint = copy.deepcopy(entrypoint)
        with sky.Dag() as dag:
            dag.add(entrypoint)
        return dag
    else:
        raise TypeError(
            'Expected a sky.Task or sky.Dag but received argument of type: '
            f'{type(entrypoint)}')


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
    DOWN = enum.auto()


def _execute(
    entrypoint: Union['sky.Task', 'sky.Dag'],
    dryrun: bool = False,
    down: bool = False,
    stream_logs: bool = True,
    handle: Any = None,
    backend: Optional[backends.Backend] = None,
    retry_until_up: bool = False,
    optimize_target: OptimizeTarget = OptimizeTarget.COST,
    stages: Optional[List[Stage]] = None,
    cluster_name: Optional[str] = None,
    detach_setup: bool = False,
    detach_run: bool = False,
    idle_minutes_to_autostop: Optional[int] = None,
    no_setup: bool = False,
    # Internal only:
    # pylint: disable=invalid-name
    _is_launched_by_spot_controller: bool = False,
) -> None:
    """Execute a entrypoint.

    If sky.Task is given or DAG has not been optimized yet, this will call
    sky.optimize() for the caller.

    Args:
      entrypoint: sky.Task or sky.Dag.
      dryrun: bool; if True, only print the provision info (e.g., cluster
        yaml).
      down: bool; whether to tear down the launched resources after all jobs
        finish (successfully or abnormally). If idle_minutes_to_autostop is
        also set, the cluster will be torn down after the specified idle time.
        Note that if errors occur during provisioning/data syncing/setting up,
        the cluster will not be torn down for debugging purposes.
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
      detach_setup: If True, run setup in non-interactive mode as part of the
        job itself. You can safely ctrl-c to detach from logging, and it will
        not interrupt the setup process. To see the logs again after detaching,
        use `sky logs`. To cancel setup, cancel the job via `sky cancel`.
      detach_run: If True, as soon as a job is submitted, return from this
        function and do not stream execution logs.
      idle_minutes_to_autostop: int; if provided, the cluster will be set to
        autostop after this many minutes of idleness.
      no_setup: bool; whether to skip setup commands or not when (re-)launching.
    """
    dag = _convert_to_dag(entrypoint)
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
    if cluster_exists:
        assert len(task.resources) == 1
        task_resources = list(task.resources)[0]
        if task_resources.cpus is not None:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'Cannot specify CPU when using an existing cluster. '
                    'CPU is only used for selecting the instance type when '
                    'creating a new cluster.')

    stages = stages if stages is not None else list(Stage)

    # Requested features that some clouds support and others don't.
    requested_features = set()

    if task.num_nodes > 1:
        requested_features.add(clouds.CloudImplementationFeatures.MULTI_NODE)

    backend = backend if backend is not None else backends.CloudVmRayBackend()
    if isinstance(backend, backends.CloudVmRayBackend):
        if down and idle_minutes_to_autostop is None:
            # Use auto{stop,down} to terminate the cluster after the task is
            # done.
            idle_minutes_to_autostop = 0
        if idle_minutes_to_autostop is not None:
            if idle_minutes_to_autostop == 0:
                # idle_minutes_to_autostop=0 can cause the following problem:
                # After we set the autostop in the PRE_EXEC stage with -i 0,
                # it could be possible that the cluster immediately found
                # itself have no task running and start the auto{stop,down}
                # process, before the task is submitted in the EXEC stage.
                verb = 'torn down' if down else 'stopped'
                logger.info(f'{colorama.Style.DIM}The cluster will '
                            f'be {verb} after 1 minutes of idleness '
                            '(after all jobs finish).'
                            f'{colorama.Style.RESET_ALL}')
                idle_minutes_to_autostop = 1
            stages.remove(Stage.DOWN)

            if not down:
                requested_features.add(
                    clouds.CloudImplementationFeatures.AUTOSTOP)

    elif idle_minutes_to_autostop is not None:
        # TODO(zhwu): Autostop is not supported for non-CloudVmRayBackend.
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                f'Backend {backend.NAME} does not support autostop, please try '
                f'{backends.CloudVmRayBackend.NAME}')

    if not cluster_exists:
        if (Stage.PROVISION in stages and task.use_spot and
                not _is_launched_by_spot_controller):
            yellow = colorama.Fore.YELLOW
            bold = colorama.Style.BRIGHT
            reset = colorama.Style.RESET_ALL
            logger.info(
                f'{yellow}Launching an unmanaged spot task, which does not '
                f'automatically recover from preemptions.{reset}\n{yellow}To '
                'get automatic recovery, use managed spot instead: '
                f'{reset}{bold}sky spot launch{reset} {yellow}or{reset} '
                f'{bold}sky.spot_launch(){reset}.')

        if Stage.OPTIMIZE in stages:
            if task.best_resources is None:
                # TODO: fix this for the situation where number of requested
                # accelerators is not an integer.
                if isinstance(backend, backends.CloudVmRayBackend):
                    # TODO: adding this check because docker backend on a
                    # no-credential machine should not enter optimize(), which
                    # would directly error out ('No cloud is enabled...').  Fix
                    # by moving `sky check` checks out of optimize()?
                    dag = sky.optimize(dag, minimize=optimize_target)
                    task = dag.tasks[0]  # Keep: dag may have been deep-copied.
                    assert task.best_resources is not None, task

    backend.register_info(dag=dag,
                          optimize_target=optimize_target,
                          requested_features=requested_features)

    if task.storage_mounts is not None:
        # Optimizer should eventually choose where to store bucket
        task.sync_storage_mounts()

    try:
        if Stage.PROVISION in stages:
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

        if Stage.SYNC_WORKDIR in stages:
            if task.workdir is not None:
                backend.sync_workdir(handle, task.workdir)

        if Stage.SYNC_FILE_MOUNTS in stages:
            backend.sync_file_mounts(handle, task.file_mounts,
                                     task.storage_mounts)

        if no_setup:
            logger.info('Setup commands skipped.')
        elif Stage.SETUP in stages:
            backend.setup(handle, task, detach_setup=detach_setup)

        if Stage.PRE_EXEC in stages:
            if idle_minutes_to_autostop is not None:
                backend.set_autostop(handle,
                                     idle_minutes_to_autostop,
                                     down=down)

        if Stage.EXEC in stages:
            try:
                global_user_state.update_last_use(handle.get_cluster_name())
                backend.execute(handle, task, detach_run)
            finally:
                # Enables post_execute() to be run after KeyboardInterrupt.
                backend.post_execute(handle, down)

        if Stage.DOWN in stages:
            if down and idle_minutes_to_autostop is None:
                backend.teardown_ephemeral_storage(task)
                backend.teardown(handle, terminate=True)
    finally:
        if cluster_name != spot.SPOT_CONTROLLER_NAME:
            # UX: print live clusters to make users aware (to save costs).
            #
            # Don't print if this job is launched by the spot controller,
            # because spot jobs are serverless, there can be many of them, and
            # users tend to continuously monitor spot jobs using `sky spot
            # status`.
            #
            # Disable the usage collection for this status command.
            env = dict(os.environ,
                       **{env_options.Options.DISABLE_LOGGING.value: '1'})
            subprocess_utils.run('sky status', env=env)
        print()
        print('\x1b[?25h', end='')  # Show cursor.


@timeline.event
@usage_lib.entrypoint
def launch(
    task: Union['sky.Task', 'sky.Dag'],
    cluster_name: Optional[str] = None,
    retry_until_up: bool = False,
    idle_minutes_to_autostop: Optional[int] = None,
    dryrun: bool = False,
    down: bool = False,
    stream_logs: bool = True,
    backend: Optional[backends.Backend] = None,
    optimize_target: OptimizeTarget = OptimizeTarget.COST,
    detach_setup: bool = False,
    detach_run: bool = False,
    no_setup: bool = False,
    # Internal only:
    # pylint: disable=invalid-name
    _is_launched_by_spot_controller: bool = False,
) -> None:
    # NOTE(dev): Keep the docstring consistent between the Python API and CLI.
    """Launch a task.

    The task's setup and run commands are executed under the task's workdir
    (when specified, it is synced to remote cluster).  The task undergoes job
    queue scheduling on the cluster.

    Currently, the first argument must be a sky.Task, or (EXPERIMENTAL advanced
    usage) a sky.Dag. In the latter case, currently it must contain a single
    task; support for pipelines/general DAGs are in experimental branches.

    Args:
        task: sky.Task, or sky.Dag (experimental; 1-task only) to launch.
        cluster_name: name of the cluster to create/reuse.  If None,
            auto-generate a name.
        retry_until_up: whether to retry launching the cluster until it is
            up.
        idle_minutes_to_autostop: automatically stop the cluster after this
            many minute of idleness, i.e., no running or pending jobs in the
            cluster's job queue. Idleness gets reset whenever setting-up/
            running/pending jobs are found in the job queue. Setting this
            flag is equivalent to running
            ``sky.launch(..., detach_run=True, ...)`` and then
            ``sky.autostop(idle_minutes=<minutes>)``. If not set, the cluster
            will not be autostopped.
        down: Tear down the cluster after all jobs finish (successfully or
            abnormally). If --idle-minutes-to-autostop is also set, the
            cluster will be torn down after the specified idle time.
            Note that if errors occur during provisioning/data syncing/setting
            up, the cluster will not be torn down for debugging purposes.
        dryrun: if True, do not actually launch the cluster.
        stream_logs: if True, show the logs in the terminal.
        backend: backend to use.  If None, use the default backend
            (CloudVMRayBackend).
        optimize_target: target to optimize for. Choices: OptimizeTarget.COST,
            OptimizeTarget.TIME.
        detach_setup: If True, run setup in non-interactive mode as part of the
            job itself. You can safely ctrl-c to detach from logging, and it
            will not interrupt the setup process. To see the logs again after
            detaching, use `sky logs`. To cancel setup, cancel the job via
            `sky cancel`. Useful for long-running setup
            commands.
        detach_run: If True, as soon as a job is submitted, return from this
            function and do not stream execution logs.
        no_setup: if True, do not re-run setup commands.

    Example:
        .. code-block:: python

            import sky
            task = sky.Task(run='echo hello SkyPilot')
            task.set_resources(
                sky.Resources(cloud=sky.AWS(), accelerators='V100:4'))
            sky.launch(task, cluster_name='my-cluster')

    """
    entrypoint = task
    backend_utils.check_cluster_name_not_reserved(cluster_name,
                                                  operation_str='sky.launch')
    _execute(
        entrypoint=entrypoint,
        dryrun=dryrun,
        down=down,
        stream_logs=stream_logs,
        handle=None,
        backend=backend,
        retry_until_up=retry_until_up,
        optimize_target=optimize_target,
        cluster_name=cluster_name,
        detach_setup=detach_setup,
        detach_run=detach_run,
        idle_minutes_to_autostop=idle_minutes_to_autostop,
        no_setup=no_setup,
        _is_launched_by_spot_controller=_is_launched_by_spot_controller,
    )


@usage_lib.entrypoint
def exec(  # pylint: disable=redefined-builtin
    task: Union['sky.Task', 'sky.Dag'],
    cluster_name: str,
    dryrun: bool = False,
    down: bool = False,
    stream_logs: bool = True,
    backend: Optional[backends.Backend] = None,
    detach_run: bool = False,
) -> None:
    # NOTE(dev): Keep the docstring consistent between the Python API and CLI.
    """Execute a task on an existing cluster.

    This function performs two actions:

    (1) workdir syncing, if the task has a workdir specified;
    (2) executing the task's ``run`` commands.

    All other steps (provisioning, setup commands, file mounts syncing) are
    skipped.  If any of those specifications changed in the task, this function
    will not reflect those changes.  To ensure a cluster's setup is up to date,
    use ``sky.launch()`` instead.

    Execution and scheduling behavior:

    - The task will undergo job queue scheduling, respecting any specified
      resource requirement. It can be executed on any node of the cluster with
      enough resources.
    - The task is run under the workdir (if specified).
    - The task is run non-interactively (without a pseudo-terminal or
      pty), so interactive commands such as ``htop`` do not work.
      Use ``ssh my_cluster`` instead.

    Args:
        task: sky.Task, or sky.Dag (experimental; 1-task only) containing the
          task to execute.
        cluster_name: name of an existing cluster to execute the task.
        down: Tear down the cluster after all jobs finish (successfully or
            abnormally). If --idle-minutes-to-autostop is also set, the
            cluster will be torn down after the specified idle time.
            Note that if errors occur during provisioning/data syncing/setting
            up, the cluster will not be torn down for debugging purposes.
        dryrun: if True, do not actually execute the task.
        stream_logs: if True, show the logs in the terminal.
        backend: backend to use.  If None, use the default backend
            (CloudVMRayBackend).
        detach_run: if True, detach from logging once the task has been
            submitted.

    Raises:
        ValueError: if the specified cluster does not exist or is not in UP
            status.
        sky.exceptions.NotSupportedError: if the specified cluster is a
            reserved cluster that does not support this operation.
    """
    entrypoint = task
    if isinstance(entrypoint, sky.Dag):
        logger.warning(
            f'{colorama.Fore.YELLOW}Passing a sky.Dag to sky.exec() is '
            'deprecated. Pass sky.Task instead.'
            f'{colorama.Style.RESET_ALL}')
    backend_utils.check_cluster_name_not_reserved(cluster_name,
                                                  operation_str='sky.exec')

    handle = backend_utils.check_cluster_available(
        cluster_name,
        operation='executing tasks',
        check_cloud_vm_ray_backend=False)
    _execute(entrypoint=entrypoint,
             dryrun=dryrun,
             down=down,
             stream_logs=stream_logs,
             handle=handle,
             backend=backend,
             stages=[
                 Stage.SYNC_WORKDIR,
                 Stage.EXEC,
             ],
             cluster_name=cluster_name,
             detach_run=detach_run)


@usage_lib.entrypoint
def spot_launch(
    task: Union['sky.Task', 'sky.Dag'],
    name: Optional[str] = None,
    stream_logs: bool = True,
    detach_run: bool = False,
    retry_until_up: bool = False,
):
    # NOTE(dev): Keep the docstring consistent between the Python API and CLI.
    """Launch a managed spot job.

    Please refer to the sky.cli.spot_launch for the document.

    Args:
        task: sky.Task, or sky.Dag (experimental; 1-task only) to launch as a
          managed spot job.
        name: Name of the spot job.
        detach_run: Whether to detach the run.

    Raises:
        ValueError: cluster does not exist.
        sky.exceptions.NotSupportedError: the feature is not supported.
    """
    entrypoint = task
    if name is None:
        name = backend_utils.generate_cluster_name()

    dag = _convert_to_dag(entrypoint)
    assert len(dag.tasks) == 1, ('Only one task is allowed in a spot launch.',
                                 dag)
    task = dag.tasks[0]
    assert len(task.resources) == 1, task
    resources = list(task.resources)[0]

    change_default_value = dict()
    if not resources.use_spot_specified:
        change_default_value['use_spot'] = True
    if resources.spot_recovery is None:
        change_default_value['spot_recovery'] = spot.SPOT_DEFAULT_STRATEGY

    new_resources = resources.copy(**change_default_value)
    task.set_resources({new_resources})

    if task.run is None:
        print(f'{colorama.Fore.GREEN}'
              'Skipping the managed spot task as the run section is not set.'
              f'{colorama.Style.RESET_ALL}')
        return

    task = _maybe_translate_local_file_mounts_and_sync_up(task)

    with tempfile.NamedTemporaryFile(prefix=f'spot-task-{name}-',
                                     mode='w') as f:
        task_config = task.to_yaml_config()
        common_utils.dump_yaml(f.name, task_config)

        controller_name = spot.SPOT_CONTROLLER_NAME
        vars_to_fill = {
            'remote_user_yaml_prefix': spot.SPOT_TASK_YAML_PREFIX,
            'user_yaml_path': f.name,
            'user_config_path': None,
            'spot_controller': controller_name,
            'cluster_name': name,
            'gcloud_installation_commands': gcp.GCLOUD_INSTALLATION_COMMAND,
            'is_dev': env_options.Options.IS_DEVELOPER.get(),
            'disable_logging': env_options.Options.DISABLE_LOGGING.get(),
            'logging_user_hash': common_utils.get_user_hash(),
            'retry_until_up': retry_until_up,
            'user': os.environ.get('USER', None),
        }
        if skypilot_config.loaded():
            # Look up the contents of the already loaded configs via the
            # 'skypilot_config' module. Don't simply read the on-disk file as
            # it may have changed since this process started.
            #
            # Pop any proxy command, because the controller would've been
            # launched behind the proxy, and in general any nodes we launch may
            # not have or need the proxy setup. (If the controller needs to
            # launch spot clusters in another region/VPC, the user should
            # properly set up VPC peering, which will allow the
            # cross-region/VPC communication. The proxy command is orthogonal
            # to this scenario.)
            #
            # This file will be uploaded to the controller node and will be
            # used throughout the spot job's recovery attempts (i.e., if it
            # relaunches due to preemption, we make sure the same config is
            # used).
            #
            # NOTE: suppose that we have a controller in old VPC, then user
            # changes 'vpc_name' in the config and does a 'spot launch'. In
            # general, the old controller may not successfully launch the job
            # in the new VPC. This happens if the two VPCs don’t have peering
            # set up. Like other places in the code, we assume properly setting
            # up networking is user's responsibilities.
            # TODO(zongheng): consider adding a basic check that checks
            # controller VPC (or name) == the spot job's VPC (or name). It may
            # not be a sufficient check (as it's always possible that peering
            # is not set up), but it may catch some obvious errors.
            # TODO(zhwu): hacky. We should pop the proxy command of the cloud
            # where the controller is launched (currently, only aws user uses
            # proxy_command).
            config_dict = skypilot_config.pop_nested(
                ('aws', 'ssh_proxy_command'))
            with tempfile.NamedTemporaryFile(mode='w', delete=False) as tmpfile:
                common_utils.dump_yaml(tmpfile.name, config_dict)
                vars_to_fill.update({
                    'user_config_path': tmpfile.name,
                    'env_var_skypilot_config':
                        skypilot_config.ENV_VAR_SKYPILOT_CONFIG,
                })

        yaml_path = backend_utils.fill_template(
            spot.SPOT_CONTROLLER_TEMPLATE,
            vars_to_fill,
            output_prefix=spot.SPOT_CONTROLLER_YAML_PREFIX)
        controller_task = task_lib.Task.from_yaml(yaml_path)
        controller_task.spot_task = task
        assert len(controller_task.resources) == 1
        print(f'{colorama.Fore.YELLOW}'
              f'Launching managed spot job {name} from spot controller...'
              f'{colorama.Style.RESET_ALL}')
        print('Launching spot controller...')
        _execute(
            entrypoint=controller_task,
            stream_logs=stream_logs,
            cluster_name=controller_name,
            detach_run=detach_run,
            idle_minutes_to_autostop=spot.
            SPOT_CONTROLLER_IDLE_MINUTES_TO_AUTOSTOP,
            retry_until_up=True,
        )


def _maybe_translate_local_file_mounts_and_sync_up(
        task: task_lib.Task) -> task_lib.Task:
    """Translates local->VM mounts into Storage->VM, then syncs up any Storage.

    Eagerly syncing up local->Storage ensures Storage->VM would work at task
    launch time.

    If there are no local source paths to be translated, this function would
    still sync up any storage mounts with local source paths (which do not
    undergo translation).
    """
    # ================================================================
    # Translate the workdir and local file mounts to cloud file mounts.
    # ================================================================
    task = copy.deepcopy(task)
    run_id = common_utils.get_usage_run_id()[:8]
    original_file_mounts = task.file_mounts if task.file_mounts else {}
    original_storage_mounts = task.storage_mounts if task.storage_mounts else {}

    copy_mounts = task.get_local_to_remote_file_mounts()
    if copy_mounts is None:
        copy_mounts = {}

    has_local_source_paths = (task.workdir is not None) or copy_mounts
    if has_local_source_paths:
        logger.info(
            f'{colorama.Fore.YELLOW}Translating file_mounts with local '
            f'source paths to SkyPilot Storage...{colorama.Style.RESET_ALL}')

    # Step 1: Translate the workdir to SkyPilot storage.
    new_storage_mounts = dict()
    if task.workdir is not None:
        bucket_name = spot.constants.SPOT_WORKDIR_BUCKET_NAME.format(
            username=getpass.getuser(), id=run_id)
        workdir = task.workdir
        task.workdir = None
        if (constants.SKY_REMOTE_WORKDIR in original_file_mounts or
                constants.SKY_REMOTE_WORKDIR in original_storage_mounts):
            raise ValueError(
                f'Cannot mount {constants.SKY_REMOTE_WORKDIR} as both the '
                'workdir and file_mounts contains it as the target.')
        new_storage_mounts[
            constants.
            SKY_REMOTE_WORKDIR] = storage_lib.Storage.from_yaml_config({
                'name': bucket_name,
                'source': workdir,
                'persistent': False,
                'mode': 'COPY',
            })
        # Check of the existence of the workdir in file_mounts is done in
        # the task construction.
        logger.info(f'Workdir {workdir!r} will be synced to cloud storage '
                    f'{bucket_name!r}.')

    # Step 2: Translate the local file mounts with folder in src to SkyPilot
    # storage.
    # TODO(zhwu): Optimize this by:
    # 1. Use the same bucket for all the mounts.
    # 2. When the src is the same, use the same bucket.
    copy_mounts_with_file_in_src = dict()
    for i, (dst, src) in enumerate(copy_mounts.items()):
        task.file_mounts.pop(dst)
        if os.path.isfile(os.path.abspath(os.path.expanduser(src))):
            copy_mounts_with_file_in_src[dst] = src
            continue
        bucket_name = spot.constants.SPOT_FM_BUCKET_NAME.format(
            username=getpass.getuser(),
            id=f'{run_id}-{i}',
        )
        new_storage_mounts[dst] = storage_lib.Storage.from_yaml_config({
            'name': bucket_name,
            'source': src,
            'persistent': False,
            'mode': 'COPY',
        })
        logger.info(
            f'Folder in local file mount {src!r} will be synced to SkyPilot '
            f'storage {bucket_name}.')

    # Step 3: Translate local file mounts with file in src to SkyPilot storage.
    # Hard link the files in src to a temporary directory, and upload folder.
    local_fm_path = os.path.join(
        tempfile.gettempdir(),
        spot.constants.SPOT_FM_LOCAL_TMP_DIR.format(id=run_id))
    os.makedirs(local_fm_path, exist_ok=True)
    file_bucket_name = spot.constants.SPOT_FM_FILE_ONLY_BUCKET_NAME.format(
        username=getpass.getuser(), id=run_id)
    if copy_mounts_with_file_in_src:
        src_to_file_id = dict()
        for i, src in enumerate(set(copy_mounts_with_file_in_src.values())):
            src_to_file_id[src] = i
            os.link(os.path.abspath(os.path.expanduser(src)),
                    os.path.join(local_fm_path, f'file-{i}'))

        new_storage_mounts[
            spot.constants.
            SPOT_FM_REMOTE_TMP_DIR] = storage_lib.Storage.from_yaml_config({
                'name': file_bucket_name,
                'source': local_fm_path,
                'persistent': False,
                'mode': 'MOUNT',
            })
        if spot.constants.SPOT_FM_REMOTE_TMP_DIR in original_storage_mounts:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'Failed to translate file mounts, due to the default '
                    f'destination {spot.constants.SPOT_FM_REMOTE_TMP_DIR} '
                    'being taken.')
        sources = list(src_to_file_id.keys())
        sources_str = '\n\t'.join(sources)
        logger.info('Source files in file_mounts will be synced to '
                    f'cloud storage {file_bucket_name}:'
                    f'\n\t{sources_str}')
    task.update_storage_mounts(new_storage_mounts)

    # Step 4: Upload storage from sources
    # Upload the local source to a bucket. The task will not be executed
    # locally, so we need to upload the files/folders to the bucket manually
    # here before sending the task to the remote spot controller.
    if task.storage_mounts:
        # There may be existing (non-translated) storage mounts, so log this
        # whenever task.storage_mounts is non-empty.
        logger.info(f'{colorama.Fore.YELLOW}Uploading sources to cloud storage.'
                    f'{colorama.Style.RESET_ALL} See: sky storage ls')
    task.sync_storage_mounts()

    # Step 5: Add the file download into the file mounts, such as
    #  /original-dst: s3://spot-fm-file-only-bucket-name/file-0
    new_file_mounts = dict()
    for dst, src in copy_mounts_with_file_in_src.items():
        storage = task.storage_mounts[spot.constants.SPOT_FM_REMOTE_TMP_DIR]
        store_type = list(storage.stores.keys())[0]
        store_prefix = storage_lib.get_store_prefix(store_type)
        bucket_url = store_prefix + file_bucket_name
        file_id = src_to_file_id[src]
        new_file_mounts[dst] = bucket_url + f'/file-{file_id}'
    task.update_file_mounts(new_file_mounts)

    # Step 6: Replace the source field that is local path in all storage_mounts
    # with bucket URI and remove the name field.
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
            if store_type == storage_lib.StoreType.S3:
                storage_obj.source = f's3://{storage_obj.name}'
            elif store_type == storage_lib.StoreType.GCS:
                storage_obj.source = f'gs://{storage_obj.name}'
            else:
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.NotSupportedError(
                        f'Unsupported store type: {store_type}')
            storage_obj.name = None
            storage_obj.force_delete = True

    return task
