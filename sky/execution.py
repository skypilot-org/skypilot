"""Execution layer.

See `Stage` for a Task's life cycle.
"""
import copy
import enum
import os
import tempfile
from typing import Any, List, Optional, Tuple, Union
import uuid

import colorama

import sky
from sky import backends
from sky import clouds
from sky import global_user_state
from sky import optimizer
from sky import sky_logging
from sky import spot
from sky import task as task_lib
from sky.backends import backend_utils
from sky.clouds import gcp
from sky.skylet import constants
from sky.usage import usage_lib
from sky.utils import common_utils
from sky.utils import controller_utils
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


def execute(
    entrypoint: Union['sky.Task', 'sky.Dag'],
    dryrun: bool = False,
    down: bool = False,
    stream_logs: bool = True,
    handle: Optional[backends.ResourceHandle] = None,
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
    # Internal only:
    # pylint: disable=invalid-name
    _is_launched_by_spot_controller: bool = False,
    _is_launched_by_sky_serve_controller: bool = False,
) -> Tuple[Optional[int], Optional[backends.ResourceHandle]]:
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
      handle: Optional[backends.ResourceHandle]; if provided, execution will use
        an existing backend cluster handle instead of provisioning a new one.
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
      job_id: Optional[int]; the job ID of the submitted job. None if the
        backend is not CloudVmRayBackend, or no job is submitted to
        the cluster.
      handle: Optional[backends.ResourceHandle]; the handle to the cluster. None
        if dryrun.
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

        if handle is None:
            assert dryrun, ('If not dryrun, handle must be set or '
                            'Stage.PROVISION must be included in stages.')
            logger.info('Dryrun finished.')
            return None, None

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
                assert isinstance(handle, backends.CloudVmRayResourceHandle)
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
        controller = controller_utils.Controllers.from_name(cluster_name)
        if controller is None and not _is_launched_by_sky_serve_controller:
            # UX: print live clusters to make users aware (to save costs).
            #
            # Don't print if this job is launched by the spot controller,
            # because spot jobs are serverless, there can be many of them, and
            # users tend to continuously monitor spot jobs using `sky spot
            # status`. Also don't print if this job is a skyserve controller
            # job or launched by a skyserve controller job, because the
            # redirect for this subprocess.run won't success and it will
            # pollute the controller logs.
            #
            # Disable the usage collection for this status command.
            env = dict(os.environ,
                       **{env_options.Options.DISABLE_LOGGING.value: '1'})
            subprocess_utils.run(
                'sky status --no-show-spot-jobs --no-show-services', env=env)
        print()
        print('\x1b[?25h', end='')  # Show cursor.
    return job_id, handle


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
    _is_launched_by_sky_serve_controller: bool = False,
) -> Tuple[Optional[int], Optional[backends.ResourceHandle]]:
    # NOTE(dev): Keep the docstring consistent between the Python API and CLI.
    """Launch a cluster or task.

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

    Returns:
      job_id: Optional[int]; the job ID of the submitted job. None if the
        backend is not CloudVmRayBackend, or no job is submitted to
        the cluster.
      handle: Optional[backends.ResourceHandle]; the handle to the cluster. None
        if dryrun.
    """
    entrypoint = task
    controller_utils.check_cluster_name_not_controller(
        cluster_name, operation_str='sky.launch')

    return execute(
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
        _is_launched_by_sky_serve_controller=
        _is_launched_by_sky_serve_controller,
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
) -> Tuple[Optional[int], Optional[backends.ResourceHandle]]:
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
            controller that does not support this operation.

    Returns:
      job_id: Optional[int]; the job ID of the submitted job. None if the
        backend is not CloudVmRayBackend, or no job is submitted to
        the cluster.
      handle: Optional[backends.ResourceHandle]; the handle to the cluster. None
        if dryrun.
    """
    entrypoint = task
    if isinstance(entrypoint, sky.Dag):
        logger.warning(
            f'{colorama.Fore.YELLOW}Passing a sky.Dag to sky.exec() is '
            'deprecated. Pass sky.Task instead.'
            f'{colorama.Style.RESET_ALL}')
    controller_utils.check_cluster_name_not_controller(cluster_name,
                                                       operation_str='sky.exec')

    handle = backend_utils.check_cluster_available(
        cluster_name,
        operation='executing tasks',
        check_cloud_vm_ray_backend=False,
        dryrun=dryrun)
    return execute(
        entrypoint=entrypoint,
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
        detach_run=detach_run,
    )


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

    dag_utils.fill_default_spot_config_in_dag_for_spot_launch(dag)

    for task_ in dag.tasks:
        controller_utils.maybe_translate_local_file_mounts_and_sync_up(
            task_, path='spot')

    with tempfile.NamedTemporaryFile(prefix=f'spot-dag-{dag.name}-',
                                     mode='w') as f:
        dag_utils.dump_chain_dag_to_yaml(dag, f.name)
        controller_name = spot.SPOT_CONTROLLER_NAME
        prefix = spot.SPOT_TASK_YAML_PREFIX
        remote_user_yaml_path = f'{prefix}/{dag.name}-{dag_uuid}.yaml'
        remote_user_config_path = f'{prefix}/{dag.name}-{dag_uuid}.config_yaml'
        extra_vars, controller_resources_config = (
            controller_utils.skypilot_config_setup(
                controller_type='spot',
                controller_resources_config=spot.constants.CONTROLLER_RESOURCES,
                remote_user_config_path=remote_user_config_path))
        try:
            controller_resources = sky.Resources.from_yaml_config(
                controller_resources_config)
        except ValueError as e:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    controller_utils.CONTROLLER_RESOURCES_NOT_VALID_MESSAGE.
                    format(controller_type='spot',
                           err=common_utils.format_exception(
                               e, use_bracket=True))) from e
        vars_to_fill = {
            'remote_user_yaml_path': remote_user_yaml_path,
            'user_yaml_path': f.name,
            'spot_controller': controller_name,
            # Note: actual spot cluster name will be <task.name>-<spot job ID>
            'dag_name': dag.name,
            'google_sdk_installation_commands':
                gcp.GOOGLE_SDK_INSTALLATION_COMMAND,
            'retry_until_up': retry_until_up,
            **extra_vars,
        }

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

        print(f'{colorama.Fore.YELLOW}'
              f'Launching managed spot job {dag.name!r} from spot controller...'
              f'{colorama.Style.RESET_ALL}')
        print('Launching spot controller...')
        execute(
            entrypoint=controller_task,
            stream_logs=stream_logs,
            cluster_name=controller_name,
            detach_run=detach_run,
            idle_minutes_to_autostop=constants.
            CONTROLLER_IDLE_MINUTES_TO_AUTOSTOP,
            retry_until_up=True,
        )
