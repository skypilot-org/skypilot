"""Controller: handles the life cycle of a managed spot cluster (job)."""
import argparse
import multiprocessing
import os
import pathlib
import time
import traceback
import typing
from typing import Tuple

import filelock

from sky import exceptions
from sky import sky_logging
from sky import status_lib
from sky.backends import backend_utils
from sky.backends import cloud_vm_ray_backend
from sky.skylet import constants
from sky.skylet import job_lib
from sky.spot import recovery_strategy
from sky.spot import spot_state
from sky.spot import spot_utils
from sky.usage import usage_lib
from sky.utils import common_utils
from sky.utils import controller_utils
from sky.utils import dag_utils
from sky.utils import subprocess_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import sky

# Use the explicit logger name so that the logger is under the
# `sky.spot.controller` namespace when executed directly, so as
# to inherit the setup from the `sky` logger.
logger = sky_logging.init_logger('sky.spot.controller')


def _get_dag_and_name(dag_yaml: str) -> Tuple['sky.Dag', str]:
    dag = dag_utils.load_chain_dag_from_yaml(dag_yaml)
    dag_name = dag.name
    assert dag_name is not None, dag
    return dag, dag_name


class SpotController:
    """Each spot controller manages the life cycle of one spot job."""

    def __init__(self, job_id: int, dag_yaml: str,
                 retry_until_up: bool) -> None:
        self._job_id = job_id
        self._dag, self._dag_name = _get_dag_and_name(dag_yaml)
        logger.info(self._dag)
        self._retry_until_up = retry_until_up
        # TODO(zhwu): this assumes the specific backend.
        self._backend = cloud_vm_ray_backend.CloudVmRayBackend()

        # pylint: disable=line-too-long
        # Add a unique identifier to the task environment variables, so that
        # the user can have the same id for multiple recoveries.
        #   Example value: sky-2022-10-04-22-46-52-467694_spot_id-17-1
        job_id_env_vars = []
        for i in range(len(self._dag.tasks)):
            job_id_env_var = common_utils.get_global_job_id(
                self._backend.run_timestamp,
                'spot',
                str(self._job_id),
                task_id=i)
            job_id_env_vars.append(job_id_env_var)

        for i, task in enumerate(self._dag.tasks):
            task_envs = task.envs or {}
            task_envs[constants.TASK_ID_ENV_VAR] = job_id_env_vars[i]
            task_envs[constants.TASK_ID_LIST_ENV_VAR] = '\n'.join(
                job_id_env_vars)
            task.update_envs(task_envs)

    def _download_log_and_stream(
            self,
            handle: cloud_vm_ray_backend.CloudVmRayResourceHandle) -> None:
        """Downloads and streams the logs of the latest job of a spot cluster.

        We do not stream the logs from the spot cluster directly, as the
        donwload and stream should be faster, and more robust against
        preemptions or ssh disconnection during the streaming.
        """
        spot_job_logs_dir = os.path.join(constants.SKY_LOGS_DIRECTORY,
                                         'spot_jobs')
        controller_utils.download_and_stream_latest_job_log(
            self._backend, handle, spot_job_logs_dir)
        logger.info(f'\n== End of logs (ID: {self._job_id}) ==')

    def _run_one_task(self, task_id: int, task: 'sky.Task') -> bool:
        """Busy loop monitoring spot cluster status and handling recovery.

        When the task is successfully completed, this function returns True,
        and will terminate the spot cluster before returning.

        If the user program fails, i.e. the task is set to FAILED or
        FAILED_SETUP, this function will return False.
        In other cases, the function will raise exceptions.
        All the failure cases will rely on the caller to clean up the spot
        cluster(s) and storages.

        Returns:
            True if the job is successfully completed; False otherwise.

        Raises:
            exceptions.ProvisionPrechecksError: This will be raised when the
                underlying `sky.launch` fails due to precheck errors only.
                I.e., none of the failover exceptions, if
                any, is due to resources unavailability. This exception
                includes the following cases:
                1. The optimizer cannot find a feasible solution.
                2. Precheck errors: invalid cluster name, failure in getting
                cloud user identity, or unsupported feature.
            exceptions.SpotJobReachedMaxRetryError: This will be raised when
                all prechecks passed but the maximum number of retries is
                reached for `sky.launch`. The failure of `sky.launch` can be
                due to:
                1. Any of the underlying failover exceptions is due to resources
                unavailability.
                2. The cluster is preempted before the job is submitted.
                3. Any unexpected error happens during the `sky.launch`.
        Other exceptions may be raised depending on the backend.
        """

        callback_func = spot_utils.event_callback_func(job_id=self._job_id,
                                                       task_id=task_id,
                                                       task=task)
        if task.run is None:
            logger.info(f'Skip running task {task_id} ({task.name}) due to its '
                        'run commands being empty.')
            # Call set_started first to initialize columns in the state table,
            # including start_at and last_recovery_at to avoid issues for
            # uninitialized columns.
            spot_state.set_started(job_id=self._job_id,
                                   task_id=task_id,
                                   start_time=time.time(),
                                   callback_func=callback_func)
            spot_state.set_succeeded(job_id=self._job_id,
                                     task_id=task_id,
                                     end_time=time.time(),
                                     callback_func=callback_func)
            return True
        usage_lib.messages.usage.update_task_id(task_id)
        task_id_env_var = task.envs[constants.TASK_ID_ENV_VAR]
        submitted_at = time.time()
        if task_id == 0:
            submitted_at = backend_utils.get_timestamp_from_run_timestamp(
                self._backend.run_timestamp)
        spot_state.set_submitted(
            self._job_id,
            task_id,
            self._backend.run_timestamp,
            submitted_at,
            resources_str=backend_utils.get_task_resources_str(task),
            callback_func=callback_func)
        logger.info(
            f'Submitted spot job {self._job_id} (task: {task_id}, name: '
            f'{task.name!r}); {constants.TASK_ID_ENV_VAR}: {task_id_env_var}')
        assert task.name is not None, task
        cluster_name = spot_utils.generate_spot_cluster_name(
            task.name, self._job_id)
        self._strategy_executor = recovery_strategy.StrategyExecutor.make(
            cluster_name, self._backend, task, self._retry_until_up)

        logger.info('Started monitoring.')
        spot_state.set_starting(job_id=self._job_id,
                                task_id=task_id,
                                callback_func=callback_func)
        remote_job_submitted_at = self._strategy_executor.launch()
        assert remote_job_submitted_at is not None, remote_job_submitted_at

        spot_state.set_started(job_id=self._job_id,
                               task_id=task_id,
                               start_time=remote_job_submitted_at,
                               callback_func=callback_func)
        while True:
            time.sleep(spot_utils.JOB_STATUS_CHECK_GAP_SECONDS)

            # Check the network connection to avoid false alarm for job failure.
            # Network glitch was observed even in the VM.
            try:
                backend_utils.check_network_connection()
            except exceptions.NetworkError:
                logger.info(
                    'Network is not available. Retrying again in '
                    f'{spot_utils.JOB_STATUS_CHECK_GAP_SECONDS} seconds.')
                continue

            # NOTE: we do not check cluster status first because race condition
            # can occur, i.e. cluster can be down during the job status check.
            job_status = spot_utils.get_job_status(self._backend, cluster_name)

            if job_status == job_lib.JobStatus.SUCCEEDED:
                end_time = spot_utils.get_job_timestamp(self._backend,
                                                        cluster_name,
                                                        get_end_time=True)
                # The job is done.
                spot_state.set_succeeded(self._job_id,
                                         task_id,
                                         end_time=end_time,
                                         callback_func=callback_func)
                logger.info(
                    f'Spot job {self._job_id} (task: {task_id}) SUCCEEDED. '
                    f'Cleaning up the spot cluster {cluster_name}.')
                # Only clean up the spot cluster, not the storages, because
                # tasks may share storages.
                recovery_strategy.terminate_cluster(cluster_name=cluster_name)
                return True

            # For single-node jobs, nonterminated job_status indicates a
            # healthy cluster. We can safely continue monitoring.
            # For multi-node jobs, since the job may not be set to FAILED
            # immediately (depending on user program) when only some of the
            # nodes are preempted, need to check the actual cluster status.
            if (job_status is not None and not job_status.is_terminal() and
                    task.num_nodes == 1):
                continue

            if job_status in [
                    job_lib.JobStatus.FAILED, job_lib.JobStatus.FAILED_SETUP
            ]:
                # Add a grace period before the check of preemption to avoid
                # false alarm for job failure.
                time.sleep(5)
            # Pull the actual cluster status from the cloud provider to
            # determine whether the cluster is preempted.
            (cluster_status,
             handle) = backend_utils.refresh_cluster_status_handle(
                 cluster_name,
                 force_refresh_statuses=set(status_lib.ClusterStatus))

            if cluster_status != status_lib.ClusterStatus.UP:
                # The cluster is (partially) preempted. It can be down, INIT
                # or STOPPED, based on the interruption behavior of the cloud.
                # Spot recovery is needed (will be done later in the code).
                cluster_status_str = ('' if cluster_status is None else
                                      f' (status: {cluster_status.value})')
                logger.info(
                    f'Cluster is preempted{cluster_status_str}. Recovering...')
            else:
                if job_status is not None and not job_status.is_terminal():
                    # The multi-node job is still running, continue monitoring.
                    continue
                elif job_status in [
                        job_lib.JobStatus.FAILED, job_lib.JobStatus.FAILED_SETUP
                ]:
                    # The user code has probably crashed, fail immediately.
                    end_time = spot_utils.get_job_timestamp(self._backend,
                                                            cluster_name,
                                                            get_end_time=True)
                    logger.info(
                        'The user job failed. Please check the logs below.\n'
                        f'== Logs of the user job (ID: {self._job_id}) ==\n')

                    self._download_log_and_stream(handle)
                    spot_status_to_set = spot_state.SpotStatus.FAILED
                    if job_status == job_lib.JobStatus.FAILED_SETUP:
                        spot_status_to_set = spot_state.SpotStatus.FAILED_SETUP
                    failure_reason = (
                        'To see the details, run: '
                        f'sky spot logs --controller {self._job_id}')

                    spot_state.set_failed(self._job_id,
                                          task_id,
                                          failure_type=spot_status_to_set,
                                          failure_reason=failure_reason,
                                          end_time=end_time,
                                          callback_func=callback_func)
                    return False
                # Although the cluster is healthy, we fail to access the
                # job status. Try to recover the job (will not restart the
                # cluster, if the cluster is healthy).
                assert job_status is None, job_status
                logger.info('Failed to fetch the job status while the '
                            'cluster is healthy. Try to recover the job '
                            '(the cluster will not be restarted).')

            # When the handle is None, the cluster should be cleaned up already.
            if handle is not None:
                resources = handle.launched_resources
                assert resources is not None, handle
                if resources.need_cleanup_after_preemption():
                    # Some spot resource (e.g., Spot TPU VM) may need to be
                    # cleaned up after preemption.
                    logger.info('Cleaning up the preempted spot cluster...')
                    recovery_strategy.terminate_cluster(cluster_name)

            # Try to recover the spot jobs, when the cluster is preempted
            # or the job status is failed to be fetched.
            spot_state.set_recovering(job_id=self._job_id,
                                      task_id=task_id,
                                      callback_func=callback_func)
            recovered_time = self._strategy_executor.recover()
            spot_state.set_recovered(self._job_id,
                                     task_id,
                                     recovered_time=recovered_time,
                                     callback_func=callback_func)

    def run(self):
        """Run controller logic and handle exceptions."""
        task_id = 0
        try:
            succeeded = True
            # We support chain DAGs only for now.
            for task_id, task in enumerate(self._dag.tasks):
                succeeded = self._run_one_task(task_id, task)
                if not succeeded:
                    break
        except exceptions.ProvisionPrechecksError as e:
            # Please refer to the docstring of self._run for the cases when
            # this exception can occur.
            failure_reason = ('; '.join(
                common_utils.format_exception(reason, use_bracket=True)
                for reason in e.reasons))
            logger.error(failure_reason)
            spot_state.set_failed(
                self._job_id,
                task_id=task_id,
                failure_type=spot_state.SpotStatus.FAILED_PRECHECKS,
                failure_reason=failure_reason,
                callback_func=spot_utils.event_callback_func(
                    job_id=self._job_id,
                    task_id=task_id,
                    task=self._dag.tasks[task_id]))
        except exceptions.SpotJobReachedMaxRetriesError as e:
            # Please refer to the docstring of self._run for the cases when
            # this exception can occur.
            logger.error(common_utils.format_exception(e))
            # The spot job should be marked as FAILED_NO_RESOURCE, as the
            # spot job may be able to launch next time.
            spot_state.set_failed(
                self._job_id,
                task_id=task_id,
                failure_type=spot_state.SpotStatus.FAILED_NO_RESOURCE,
                failure_reason=common_utils.format_exception(e),
                callback_func=spot_utils.event_callback_func(
                    job_id=self._job_id,
                    task_id=task_id,
                    task=self._dag.tasks[task_id]))
        except (Exception, SystemExit) as e:  # pylint: disable=broad-except
            with ux_utils.enable_traceback():
                logger.error(traceback.format_exc())
            msg = ('Unexpected error occurred: '
                   f'{common_utils.format_exception(e, use_bracket=True)}')
            logger.error(msg)
            spot_state.set_failed(
                self._job_id,
                task_id=task_id,
                failure_type=spot_state.SpotStatus.FAILED_CONTROLLER,
                failure_reason=msg,
                callback_func=spot_utils.event_callback_func(
                    job_id=self._job_id,
                    task_id=task_id,
                    task=self._dag.tasks[task_id]))
        finally:
            # This will set all unfinished tasks to CANCELLING, and will not
            # affect the jobs in terminal states.
            # We need to call set_cancelling before set_cancelled to make sure
            # the table entries are correctly set.
            callback_func = spot_utils.event_callback_func(
                job_id=self._job_id,
                task_id=task_id,
                task=self._dag.tasks[task_id])
            spot_state.set_cancelling(job_id=self._job_id,
                                      callback_func=callback_func)
            spot_state.set_cancelled(job_id=self._job_id,
                                     callback_func=callback_func)


def _run_controller(job_id: int, dag_yaml: str, retry_until_up: bool):
    """Runs the controller in a remote process for interruption."""
    # The controller needs to be instantiated in the remote process, since
    # the controller is not serializable.
    spot_controller = SpotController(job_id, dag_yaml, retry_until_up)
    spot_controller.run()


def _handle_signal(job_id):
    """Handle the signal if the user sent it."""
    signal_file = pathlib.Path(spot_utils.SIGNAL_FILE_PREFIX.format(job_id))
    user_signal = None
    if signal_file.exists():
        # Filelock is needed to prevent race condition with concurrent
        # signal writing.
        # TODO(mraheja): remove pylint disabling when filelock version
        # updated
        # pylint: disable=abstract-class-instantiated
        with filelock.FileLock(str(signal_file) + '.lock'):
            with signal_file.open(mode='r', encoding='utf-8') as f:
                user_signal = f.read().strip()
                try:
                    user_signal = spot_utils.UserSignal(user_signal)
                except ValueError:
                    logger.warning(
                        f'Unknown signal received: {user_signal}. Ignoring.')
                    user_signal = None
            # Remove the signal file, after reading the signal.
            signal_file.unlink()
    if user_signal is None:
        # None or empty string.
        return
    assert user_signal == spot_utils.UserSignal.CANCEL, (
        f'Only cancel signal is supported, but {user_signal} got.')
    raise exceptions.SpotUserCancelledError(
        f'User sent {user_signal.value} signal.')


def _cleanup(job_id: int, dag_yaml: str):
    """Clean up the spot cluster(s) and storages.

    (1) Clean up the succeeded task(s)' ephemeral storage. The storage has
        to be cleaned up after the whole job is finished, as the tasks
        may share the same storage.
    (2) Clean up the spot cluster(s) that are not cleaned up yet, which
        can happen when the spot task failed or cancelled. At most one
        spot cluster should be left when reaching here, as we currently
        only support chain DAGs, and only spot task is executed at a time.
    """
    # NOTE: The code to get cluster name is same as what we did in the spot
    # controller, we should keep it in sync with SpotController.__init__()
    dag, _ = _get_dag_and_name(dag_yaml)
    for task in dag.tasks:
        cluster_name = spot_utils.generate_spot_cluster_name(task.name, job_id)
        recovery_strategy.terminate_cluster(cluster_name)
        # Clean up Storages with persistent=False.
        # TODO(zhwu): this assumes the specific backend.
        backend = cloud_vm_ray_backend.CloudVmRayBackend()
        backend.teardown_ephemeral_storage(task)


def start(job_id, dag_yaml, retry_until_up):
    """Start the controller."""
    controller_process = None
    cancelling = False
    try:
        _handle_signal(job_id)
        # TODO(suquark): In theory, we should make controller process a
        #  daemon process so it will be killed after this process exits,
        #  however daemon process cannot launch subprocesses, explained here:
        #  https://docs.python.org/3/library/multiprocessing.html#multiprocessing.Process.daemon  # pylint: disable=line-too-long
        #  So we can only enable daemon after we no longer need to
        #  start daemon processes like Ray.
        controller_process = multiprocessing.Process(target=_run_controller,
                                                     args=(job_id, dag_yaml,
                                                           retry_until_up))
        controller_process.start()
        while controller_process.is_alive():
            _handle_signal(job_id)
            time.sleep(1)
    except exceptions.SpotUserCancelledError:
        dag, _ = _get_dag_and_name(dag_yaml)
        task_id, _ = (spot_state.get_latest_task_id_status(job_id))
        logger.info(
            f'Cancelling spot job, job_id: {job_id}, task_id: {task_id}')
        spot_state.set_cancelling(job_id=job_id,
                                  callback_func=spot_utils.event_callback_func(
                                      job_id=job_id,
                                      task_id=task_id,
                                      task=dag.tasks[task_id]))
        cancelling = True
    finally:
        if controller_process is not None:
            logger.info(f'Killing controller process {controller_process.pid}.')
            # NOTE: it is ok to kill or join a killed process.
            # Kill the controller process first; if its child process is
            # killed first, then the controller process will raise errors.
            # Kill any possible remaining children processes recursively.
            subprocess_utils.kill_children_processes(controller_process.pid,
                                                     force=True)
            controller_process.join()
            logger.info(f'Controller process {controller_process.pid} killed.')

        logger.info(f'Cleaning up any spot cluster for job {job_id}.')
        # NOTE: Originally, we send an interruption signal to the controller
        # process and the controller process handles cleanup. However, we
        # figure out the behavior differs from cloud to cloud
        # (e.g., GCP ignores 'SIGINT'). A possible explanation is
        # https://unix.stackexchange.com/questions/356408/strange-problem-with-trap-and-sigint
        # But anyway, a clean solution is killing the controller process
        # directly, and then cleanup the cluster state.
        _cleanup(job_id, dag_yaml=dag_yaml)
        logger.info(f'Spot cluster of job {job_id} has been cleaned up.')

        if cancelling:
            spot_state.set_cancelled(
                job_id=job_id,
                callback_func=spot_utils.event_callback_func(
                    job_id=job_id, task_id=task_id, task=dag.tasks[task_id]))

        # We should check job status after 'set_cancelled', otherwise
        # the job status is not terminal.
        job_status = spot_state.get_status(job_id)
        assert job_status is not None
        # The job can be non-terminal if the controller exited abnormally,
        # e.g. failed to launch cluster after reaching the MAX_RETRY.
        if not job_status.is_terminal():
            logger.info(f'Previous spot job status: {job_status.value}')
            spot_state.set_failed(
                job_id,
                task_id=None,
                failure_type=spot_state.SpotStatus.FAILED_CONTROLLER,
                failure_reason=('Unexpected error occurred. For details, '
                                f'run: sky spot logs --controller {job_id}'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--job-id',
                        required=True,
                        type=int,
                        help='Job id for the controller job.')
    parser.add_argument('--retry-until-up',
                        action='store_true',
                        help='Retry until the spot cluster is up.')
    parser.add_argument('dag_yaml',
                        type=str,
                        help='The path to the user spot task yaml file.')
    args = parser.parse_args()
    # We start process with 'spawn', because 'fork' could result in weird
    # behaviors; 'spawn' is also cross-platform.
    multiprocessing.set_start_method('spawn', force=True)
    start(args.job_id, args.dag_yaml, args.retry_until_up)
