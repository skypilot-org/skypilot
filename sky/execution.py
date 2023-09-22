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
import os
import re
import tempfile
import time
from typing import Any, Dict, List, Optional, Union
import uuid

import colorama
import filelock

import sky
from sky import backends
from sky import clouds
from sky import exceptions
from sky import global_user_state
from sky import optimizer
from sky import serve
from sky import sky_logging
from sky import skypilot_config
from sky import spot
from sky import status_lib
from sky import task as task_lib
from sky.backends import backend_utils
from sky.clouds import gcp
from sky.data import data_utils
from sky.data import storage as storage_lib
from sky.skylet import constants
from sky.skylet import job_lib
from sky.usage import usage_lib
from sky.utils import common_utils
from sky.utils import dag_utils
from sky.utils import env_options
from sky.utils import rich_utils
from sky.utils import subprocess_utils
from sky.utils import timeline
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

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
            dag.name = entrypoint.name
        return dag
    else:
        raise TypeError(
            'Expected a sky.Task or sky.Dag but received argument of type: '
            f'{type(entrypoint)}')


class Stage(enum.Enum):
    """Stages for a run of a sky.Task."""
    # TODO: rename actual methods to be consistent.
    CLONE_DISK = enum.auto()
    OPTIMIZE = enum.auto()
    PROVISION = enum.auto()
    SYNC_WORKDIR = enum.auto()
    SYNC_FILE_MOUNTS = enum.auto()
    SETUP = enum.auto()
    PRE_EXEC = enum.auto()
    EXEC = enum.auto()
    DOWN = enum.auto()


def _maybe_clone_disk_from_cluster(clone_disk_from: Optional[str],
                                   cluster_name: Optional[str],
                                   task: 'sky.Task') -> 'sky.Task':
    if clone_disk_from is None:
        return task
    task, handle = backend_utils.check_can_clone_disk_and_override_task(
        clone_disk_from, cluster_name, task)
    original_cloud = handle.launched_resources.cloud
    assert original_cloud is not None, handle.launched_resources
    task_resources = list(task.resources)[0]

    with rich_utils.safe_status('Creating image from source cluster '
                                f'{clone_disk_from!r}'):
        image_id = original_cloud.create_image_from_cluster(
            clone_disk_from,
            handle.cluster_name_on_cloud,
            region=handle.launched_resources.region,
            zone=handle.launched_resources.zone,
        )
        rich_utils.force_update_status(
            f'Migrating image {image_id} to target region '
            f'{task_resources.region}...')
        source_region = handle.launched_resources.region
        target_region = task_resources.region
        assert source_region is not None, handle.launched_resources
        assert target_region is not None, task_resources

        image_id = original_cloud.maybe_move_image(
            image_id,
            source_region=source_region,
            target_region=target_region,
            source_zone=handle.launched_resources.zone,
            target_zone=task_resources.zone,
        )
    logger.info(
        f'{colorama.Fore.GREEN}'
        f'Successfully created image {image_id!r} for {clone_disk_from!r} '
        f'on {original_cloud}.{colorama.Style.RESET_ALL}\n'
        'Overriding task\'s image_id.')
    task_resources = task_resources.copy(image_id=image_id,
                                         _is_image_managed=True)
    task.set_resources(task_resources)
    # Set the best_resources to None to trigger a re-optimization, so that
    # the new task_resources is used.
    task.best_resources = None
    logger.debug(f'Overridden task resources: {task.resources}')
    return task


def _execute(
    entrypoint: Union['sky.Task', 'sky.Dag'],
    dryrun: bool = False,
    down: bool = False,
    stream_logs: bool = True,
    handle: Any = None,
    backend: Optional[backends.Backend] = None,
    retry_until_up: bool = False,
    optimize_target: optimizer.OptimizeTarget = optimizer.OptimizeTarget.COST,
    stages: Optional[List[Stage]] = None,
    cluster_name: Optional[str] = None,
    detach_setup: bool = False,
    detach_run: bool = False,
    idle_minutes_to_autostop: Optional[int] = None,
    no_setup: bool = False,
    clone_disk_from: Optional[str] = None,
    minimize_logging: bool = False,
    # Internal only:
    # pylint: disable=invalid-name
    _is_launched_by_spot_controller: bool = False,
) -> Optional[int]:
    """Execute an entrypoint.

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

    Returns:
      A job id (int) if the job is submitted successfully and backend is
      CloudVmRayBackend, otherwise None.
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
        # TODO(woosuk): If the cluster exists, print a warning that
        # `cpus` and `memory` are not used as a job scheduling constraint,
        # unlike `gpus`.

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
                # TODO(ewzeng): allow autostop for spot when stopping is
                # supported.
                if task.use_spot:
                    with ux_utils.print_exception_no_traceback():
                        raise ValueError(
                            'Autostop is not supported for spot instances.')

    elif idle_minutes_to_autostop is not None:
        # TODO(zhwu): Autostop is not supported for non-CloudVmRayBackend.
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                f'Backend {backend.NAME} does not support autostop, please try '
                f'{backends.CloudVmRayBackend.NAME}')

    if Stage.CLONE_DISK in stages:
        task = _maybe_clone_disk_from_cluster(clone_disk_from, cluster_name,
                                              task)

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
                          requested_features=requested_features,
                          minimize_logging=minimize_logging)

    if task.storage_mounts is not None:
        # Optimizer should eventually choose where to store bucket
        task.sync_storage_mounts()

    job_id = None
    try:
        if Stage.PROVISION in stages:
            if handle is None:
                handle = backend.provision(task,
                                           task.best_resources,
                                           dryrun=dryrun,
                                           stream_logs=stream_logs,
                                           cluster_name=cluster_name,
                                           retry_until_up=retry_until_up)

        if dryrun and handle is None:
            logger.info('Dryrun finished.')
            return None

        if Stage.SYNC_WORKDIR in stages and not dryrun:
            if task.workdir is not None:
                backend.sync_workdir(handle, task.workdir)

        if Stage.SYNC_FILE_MOUNTS in stages and not dryrun:
            backend.sync_file_mounts(handle, task.file_mounts,
                                     task.storage_mounts)

        if no_setup:
            logger.info('Setup commands skipped.')
        elif Stage.SETUP in stages and not dryrun:
            backend.setup(handle, task, detach_setup=detach_setup)

        if Stage.PRE_EXEC in stages and not dryrun:
            if idle_minutes_to_autostop is not None:
                assert isinstance(backend, backends.CloudVmRayBackend)
                backend.set_autostop(handle,
                                     idle_minutes_to_autostop,
                                     down=down)

        if Stage.EXEC in stages:
            try:
                global_user_state.update_last_use(handle.get_cluster_name())
                job_id = backend.execute(handle,
                                         task,
                                         detach_run,
                                         dryrun=dryrun)
            finally:
                # Enables post_execute() to be run after KeyboardInterrupt.
                backend.post_execute(handle, down)

        if Stage.DOWN in stages and not dryrun:
            if down and idle_minutes_to_autostop is None:
                backend.teardown_ephemeral_storage(task)
                backend.teardown(handle, terminate=True)
    finally:
        if not minimize_logging:
            # UX: print live clusters to make users aware (to save costs).
            #
            # Don't print if this job is launched by the spot controller,
            # because spot jobs are serverless, there can be many of them, and
            # users tend to continuously monitor spot jobs using `sky spot
            # status`. Also don't print if this job is a skyserve controller
            # job.
            #
            # Disable the usage collection for this status command.
            env = dict(os.environ,
                       **{env_options.Options.DISABLE_LOGGING.value: '1'})
            subprocess_utils.run('sky status --no-show-spot-jobs', env=env)
        print()
        print('\x1b[?25h', end='')  # Show cursor.
    return job_id


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
    optimize_target: optimizer.OptimizeTarget = optimizer.OptimizeTarget.COST,
    detach_setup: bool = False,
    detach_run: bool = False,
    no_setup: bool = False,
    clone_disk_from: Optional[str] = None,
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
        clone_disk_from: [Experimental] if set, clone the disk from the
            specified cluster. This is useful to migrate the cluster to a
            different availability zone or region.

    Example:
        .. code-block:: python

            import sky
            task = sky.Task(run='echo hello SkyPilot')
            task.set_resources(
                sky.Resources(cloud=sky.AWS(), accelerators='V100:4'))
            sky.launch(task, cluster_name='my-cluster')

    Raises:
        exceptions.ClusterOwnerIdentityMismatchError: if the cluster is
            owned by another user.
        exceptions.InvalidClusterNameError: if the cluster name is invalid.
        exceptions.ResourcesMismatchError: if the requested resources
            do not match the existing cluster.
        exceptions.NotSupportedError: if required features are not supported
            by the backend/cloud/cluster.
        exceptions.ResourcesUnavailableError: if the requested resources
            cannot be satisfied. The failover_history of the exception
            will be set as:
                1. Empty: iff the first-ever sky.optimize() fails to
                find a feasible resource; no pre-check or actual launch is
                attempted.
                2. Non-empty: iff at least 1 exception from either
                our pre-checks (e.g., cluster name invalid) or a region/zone
                throwing resource unavailability.
        exceptions.CommandError: any ssh command error.
        exceptions.NoCloudAccessError: if all clouds are disabled.
    Other exceptions may be raised depending on the backend.
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
        clone_disk_from=clone_disk_from,
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
        check_cloud_vm_ray_backend=False,
        dryrun=dryrun)
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


def _shared_controller_env_vars() -> Dict[str, Any]:
    return {
        'SKYPILOT_USER_ID': common_utils.get_user_hash(),
        'SKYPILOT_SKIP_CLOUD_IDENTITY_CHECK': 1,
        # Should not use $USER here, as that env var can be empty when
        # running in a container.
        'SKYPILOT_USER': getpass.getuser(),
        'SKYPILOT_DEV': env_options.Options.IS_DEVELOPER.get(),
        'SKYPILOT_DEBUG': env_options.Options.SHOW_DEBUG_INFO.get(),
        'SKYPILOT_DISABLE_USAGE_COLLECTION':
            env_options.Options.DISABLE_LOGGING.get(),
    }


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
    dag_uuid = str(uuid.uuid4().hex[:4])

    dag = _convert_to_dag(entrypoint)
    assert dag.is_chain(), ('Only single-task or chain DAG is '
                            'allowed for spot_launch.', dag)

    dag_utils.maybe_infer_and_fill_dag_and_task_names(dag)

    task_names = set()
    for task_ in dag.tasks:
        if task_.name in task_names:
            raise ValueError(
                f'Task name {task_.name!r} is duplicated in the DAG. Either '
                'change task names to be unique, or specify the DAG name only '
                'and comment out the task names (so that they will be auto-'
                'generated) .')
        task_names.add(task_.name)

    dag_utils.fill_default_spot_config_in_dag(dag)

    for task_ in dag.tasks:
        _maybe_translate_local_file_mounts_and_sync_up(task_)

    with tempfile.NamedTemporaryFile(prefix=f'spot-dag-{dag.name}-',
                                     mode='w') as f:
        dag_utils.dump_chain_dag_to_yaml(dag, f.name)
        controller_name = spot.SPOT_CONTROLLER_NAME
        vars_to_fill = {
            'remote_user_yaml_prefix': spot.SPOT_TASK_YAML_PREFIX,
            'user_yaml_path': f.name,
            'user_config_path': None,
            'spot_controller': controller_name,
            # Note: actual spot cluster name will be <task.name>-<spot job ID>
            'dag_name': dag.name,
            'uuid': dag_uuid,
            'google_sdk_installation_commands':
                gcp.GOOGLE_SDK_INSTALLATION_COMMAND,
            'retry_until_up': retry_until_up,
        }
        controller_resources_config = copy.copy(
            spot.constants.CONTROLLER_RESOURCES)
        spot_env_vars = _shared_controller_env_vars()
        if skypilot_config.loaded():
            # Look up the contents of the already loaded configs via the
            # 'skypilot_config' module. Don't simply read the on-disk file as
            # it may have changed since this process started.
            #
            # Set any proxy command to None, because the controller would've
            # been launched behind the proxy, and in general any nodes we
            # launch may not have or need the proxy setup. (If the controller
            # needs to launch spot clusters in another region/VPC, the user
            # should properly set up VPC peering, which will allow the
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
            # TODO(zhwu): hacky. We should only set the proxy command of the
            # cloud where the controller is launched (currently, only aws user
            # uses proxy_command).
            proxy_command_key = ('aws', 'ssh_proxy_command')
            ssh_proxy_command = skypilot_config.get_nested(
                proxy_command_key, None)
            config_dict = skypilot_config.to_dict()
            if isinstance(ssh_proxy_command, str):
                config_dict = skypilot_config.set_nested(
                    proxy_command_key, None)
            elif isinstance(ssh_proxy_command, dict):
                # Instead of removing the key, we set the value to empty string
                # so that the controller will only try the regions specified by
                # the keys.
                ssh_proxy_command = {k: None for k in ssh_proxy_command}
                config_dict = skypilot_config.set_nested(
                    proxy_command_key, ssh_proxy_command)

            with tempfile.NamedTemporaryFile(mode='w', delete=False) as tmpfile:
                prefix = spot.SPOT_TASK_YAML_PREFIX
                remote_user_config_path = (
                    f'{prefix}/{dag.name}-{dag_uuid}.config_yaml')
                common_utils.dump_yaml(tmpfile.name, config_dict)
                vars_to_fill.update({
                    'user_config_path': tmpfile.name,
                    'remote_user_config_path': remote_user_config_path,
                })
                spot_env_vars[skypilot_config.ENV_VAR_SKYPILOT_CONFIG] = (
                    remote_user_config_path)

            # Override the controller resources with the ones specified in the
            # config.
            custom_controller_resources_config = skypilot_config.get_nested(
                ('spot', 'controller', 'resources'), None)
            if custom_controller_resources_config is not None:
                controller_resources_config.update(
                    custom_controller_resources_config)
        try:
            controller_resources = sky.Resources.from_yaml_config(
                controller_resources_config)
        except ValueError as e:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'Spot controller resources is not valid, please check '
                    '~/.sky/config.yaml file and make sure '
                    'spot.controller.resources is a valid resources spec. '
                    'Details:\n'
                    f'  {common_utils.format_exception(e, use_bracket=True)}'
                ) from e

        yaml_path = os.path.join(spot.SPOT_CONTROLLER_YAML_PREFIX,
                                 f'{name}-{dag_uuid}.yaml')
        backend_utils.fill_template(spot.SPOT_CONTROLLER_TEMPLATE,
                                    vars_to_fill,
                                    output_path=yaml_path)
        controller_task = task_lib.Task.from_yaml(yaml_path)
        assert len(controller_task.resources) == 1, controller_task
        # Backward compatibility: if the user changed the
        # spot-controller.yaml.j2 to customize the controller resources,
        # we should use it.
        controller_task_resources = list(controller_task.resources)[0]
        if not controller_task_resources.is_empty():
            controller_resources = controller_task_resources
        controller_task.set_resources(controller_resources)

        controller_task.spot_dag = dag
        assert len(controller_task.resources) == 1

        controller_task.update_envs(spot_env_vars)

        print(f'{colorama.Fore.YELLOW}'
              f'Launching managed spot job {dag.name!r} from spot controller...'
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
            minimize_logging=True,
        )


def _maybe_translate_local_file_mounts_and_sync_up(task: task_lib.Task):
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
    run_id = common_utils.get_usage_run_id()[:8]
    original_file_mounts = task.file_mounts if task.file_mounts else {}
    original_storage_mounts = task.storage_mounts if task.storage_mounts else {}

    copy_mounts = task.get_local_to_remote_file_mounts()
    if copy_mounts is None:
        copy_mounts = {}

    has_local_source_paths_file_mounts = bool(copy_mounts)
    has_local_source_paths_workdir = task.workdir is not None

    msg = None
    if has_local_source_paths_workdir and has_local_source_paths_file_mounts:
        msg = 'workdir and file_mounts with local source paths'
    elif has_local_source_paths_file_mounts:
        msg = 'file_mounts with local source paths'
    elif has_local_source_paths_workdir:
        msg = 'workdir'
    if msg:
        logger.info(f'{colorama.Fore.YELLOW}Translating {msg} to SkyPilot '
                    f'Storage...{colorama.Style.RESET_ALL}')

    # Step 1: Translate the workdir to SkyPilot storage.
    new_storage_mounts = {}
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
    copy_mounts_with_file_in_src = {}
    for i, (dst, src) in enumerate(copy_mounts.items()):
        assert task.file_mounts is not None
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
        src_to_file_id = {}
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
    new_file_mounts = {}
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
            elif store_type == storage_lib.StoreType.R2:
                storage_obj.source = f'r2://{storage_obj.name}'
            else:
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.NotSupportedError(
                        f'Unsupported store type: {store_type}')
            storage_obj.force_delete = True


@usage_lib.entrypoint
def serve_up(
    task: 'sky.Task',
    service_name: Optional[str] = None,
) -> None:
    """Spin up a service.

    Please refer to the sky.cli.serve_up for the document.

    Args:
        task: sky.Task to serve up.
        service_name: Name of the service.
    """
    if service_name is None:
        service_name = backend_utils.generate_service_name()

    if re.fullmatch(serve.SERVICE_NAME_VALID_REGEX, service_name) is None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'Service name {service_name!r} is invalid: '
                             f'ensure it is fully matched by regex (e.g., '
                             'only contains lower letters, numbers and dash): '
                             f'{serve.SERVICE_NAME_VALID_REGEX}')

    if global_user_state.get_service_from_name(service_name) is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'Service name {service_name!r} is already '
                             'taken. Please use a different name.')

    if task.service is None:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError('Service section not found.')
    controller_resources_config: Dict[str, Any] = copy.copy(
        serve.CONTROLLER_RESOURCES)
    if task.service.controller_resources is not None:
        controller_resources_config.update(task.service.controller_resources)
    if 'ports' in controller_resources_config:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Cannot specify ports for controller resources.')
    try:
        controller_resources = sky.Resources.from_yaml_config(
            controller_resources_config)
    except ValueError as e:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                'Encountered error when parsing controller resources') from e

    assert task.service is not None, task
    assert len(task.resources) == 1, task
    requested_resources = list(task.resources)[0]
    if requested_resources.ports is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                'Specifying ports in resources is not allowed. SkyServe will '
                'use the port specified in the service section.')

    task.set_resources(requested_resources.copy(ports=[task.service.app_port]))

    service_handle = serve.ServiceHandle(
        service_name=service_name,
        policy=task.service.policy_str(),
        requested_resources=requested_resources,
        requested_controller_resources=controller_resources,
        auto_restart=task.service.auto_restart)
    # Use filelock here to make sure only one process can write to database
    # at the same time. Then we generate available controller name again to
    # make sure even in race condition, we can still get the correct controller
    # name.
    # In the same time, generate ports for the controller and load balancer.
    # Use file lock to make sure the ports are unique to each service.
    try:
        # TODO(tian): remove pylint disabling when filelock
        # version updated
        # pylint: disable=abstract-class-instantiated
        with filelock.FileLock(
                os.path.expanduser(serve.CONTROLLER_FILE_LOCK_PATH),
                serve.CONTROLLER_FILE_LOCK_TIMEOUT):
            controller_name, _ = serve.get_available_controller_name(
                controller_resources)
            global_user_state.add_or_update_service(
                service_name,
                launched_at=int(time.time()),
                controller_name=controller_name,
                handle=service_handle,
                status=status_lib.ServiceStatus.CONTROLLER_INIT)

            controller_port, load_balancer_port = (
                serve.gen_ports_for_serve_process(controller_name))
            service_handle.controller_port = controller_port
            service_handle.load_balancer_port = load_balancer_port
            global_user_state.set_service_handle(service_name, service_handle)
            controller_resources = controller_resources.copy(
                ports=[load_balancer_port])
    except filelock.Timeout as e:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(
                'Timeout when obtaining controller lock for service '
                f'{service_name!r}. Please check if there are some '
                '`sky serve up` process hanging abnormally.') from e

    # TODO(tian): Use skyserve constants, or maybe refactor these constants
    # out of spot constants since their name is mostly not spot-specific.
    _maybe_translate_local_file_mounts_and_sync_up(task)
    ephemeral_storage = []
    if task.storage_mounts is not None:
        for storage in task.storage_mounts.values():
            if not storage.persistent:
                ephemeral_storage.append(storage.to_yaml_config())
    service_handle.ephemeral_storage = ephemeral_storage
    global_user_state.set_service_handle(service_name, service_handle)

    with tempfile.NamedTemporaryFile(prefix=f'serve-task-{service_name}-',
                                     mode='w') as f:
        task_config = task.to_yaml_config()
        if ('resources' in task_config and
                'spot_recovery' in task_config['resources']):
            del task_config['resources']['spot_recovery']
        common_utils.dump_yaml(f.name, task_config)
        remote_task_yaml_path = serve.generate_remote_task_yaml_file_name(
            service_name)
        controller_log_file = (
            serve.generate_remote_controller_log_file_name(service_name))
        load_balancer_log_file = (
            serve.generate_remote_load_balancer_log_file_name(service_name))
        vars_to_fill = {
            'remote_task_yaml_path': remote_task_yaml_path,
            'local_task_yaml_path': f.name,
            'google_sdk_installation_commands':
                gcp.GOOGLE_SDK_INSTALLATION_COMMAND,
            'service_dir': serve.generate_remote_service_dir_name(service_name),
            'service_name': service_name,
            'controller_port': controller_port,
            'load_balancer_port': load_balancer_port,
            'app_port': task.service.app_port,
            'controller_log_file': controller_log_file,
            'load_balancer_log_file': load_balancer_log_file,
        }
        controller_yaml_path = serve.generate_controller_yaml_file_name(
            service_name)
        backend_utils.fill_template(serve.CONTROLLER_TEMPLATE,
                                    vars_to_fill,
                                    output_path=controller_yaml_path)
        controller_task = task_lib.Task.from_yaml(controller_yaml_path)
        controller_task.set_resources(controller_resources)

        # Set this flag to modify default ray task CPU usage to custom value
        # instead of default 0.5 vCPU. We need to set it to a smaller value
        # to support a larger number of services.
        controller_task.is_sky_serve_controller_task = True

        controller_task.update_envs(_shared_controller_env_vars())

        fore = colorama.Fore
        style = colorama.Style
        print(f'\n{fore.YELLOW}Launching controller for {service_name!r}...'
              f'{style.RESET_ALL}')
        job_id = _execute(
            entrypoint=controller_task,
            stream_logs=False,
            cluster_name=controller_name,
            detach_run=True,
            # We use autostop here to reduce cold start time, since in most
            # cases the controller resources requirement will be the default
            # value and a previous controller could be reused.
            idle_minutes_to_autostop=serve.CONTROLLER_IDLE_MINUTES_TO_AUTOSTOP,
            retry_until_up=True,
            minimize_logging=True,
        )

        controller_record = global_user_state.get_cluster_from_name(
            controller_name)
        if controller_record is None:
            global_user_state.set_service_status(
                service_name, status_lib.ServiceStatus.CONTROLLER_FAILED)
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError(
                    'Controller failed to launch. Please check the logs above.')
        handle = controller_record['handle']
        assert isinstance(handle, backends.CloudVmRayResourceHandle)
        backend = backend_utils.get_backend_from_handle(handle)
        assert isinstance(backend, backends.CloudVmRayBackend), backend
        backend.register_info(minimize_logging=True)
        service_handle.endpoint_ip = handle.head_ip
        global_user_state.set_service_handle(service_name, service_handle)

        def _wait_until_job_is_running_on_controller(
                job_id: Optional[int]) -> bool:
            if job_id is None:
                return False
            for _ in range(serve.SERVE_STARTUP_TIMEOUT):
                job_statuses = backend.get_job_status(handle, [job_id],
                                                      stream_logs=False)
                job_status = job_statuses.get(str(job_id), None)
                if job_status == job_lib.JobStatus.RUNNING:
                    return True
                time.sleep(1)
            # Cancel any jobs that are still pending after timeout.
            if job_status == job_lib.JobStatus.PENDING:
                backend.cancel_jobs(handle, jobs=[job_id])
            return False

        if not _wait_until_job_is_running_on_controller(job_id):
            global_user_state.set_service_status(
                service_name, status_lib.ServiceStatus.CONTROLLER_FAILED)
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError(
                    f'Controller failed to launch. Please check '
                    f'the logs with sky serve logs {service_name} '
                    '--controller')

        service_handle.job_id = job_id
        global_user_state.set_service_handle(service_name, service_handle)
        print(f'{fore.GREEN}Launching controller for {service_name!r}...done.'
              f'{style.RESET_ALL}')

        global_user_state.set_service_status(
            service_name, status_lib.ServiceStatus.REPLICA_INIT)

        print(f'\n{fore.CYAN}Service name: '
              f'{style.BRIGHT}{service_name}{style.RESET_ALL}'
              '\nTo see detailed info:'
              f'\t\t{backend_utils.BOLD}sky serve status {service_name} (-a)'
              f'{backend_utils.RESET_BOLD}'
              '\nTo see logs of one replica:'
              f'\t{backend_utils.BOLD}sky serve logs {service_name} '
              f'[REPLICA_ID]{backend_utils.RESET_BOLD}'
              '\nTo see logs of load balancer:'
              f'\t{backend_utils.BOLD}sky serve logs --load-balancer '
              f'{service_name}{backend_utils.RESET_BOLD}'
              '\nTo see logs of controller:'
              f'\t{backend_utils.BOLD}sky serve logs --controller '
              f'{service_name}{backend_utils.RESET_BOLD}'
              '\nTo teardown the service:'
              f'\t{backend_utils.BOLD}sky serve down {service_name}'
              f'{backend_utils.RESET_BOLD}'
              f'\n(use {backend_utils.BOLD}sky serve status {service_name}'
              f'{backend_utils.RESET_BOLD} to get all valid REPLICA_ID)')
        print(f'\n{style.BRIGHT}{fore.CYAN}Endpoint URL: '
              f'{style.RESET_ALL}{fore.CYAN}'
              f'{handle.head_ip}:{load_balancer_port}{style.RESET_ALL}')
        print(f'{fore.GREEN}Starting replicas now...{style.RESET_ALL}')
        print('Please use the above command to find the latest status.')
