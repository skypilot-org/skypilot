"""Controller: handles the life cycle of a managed spot cluster (job)."""
import argparse
import multiprocessing
import pathlib
import time
import traceback
from typing import Tuple

import filelock

import sky
from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky.backends import backend_utils
from sky.backends import cloud_vm_ray_backend
from sky.skylet import constants
from sky.skylet import job_lib
from sky.spot import recovery_strategy
from sky.spot import spot_state
from sky.spot import spot_utils
from sky.utils import common_utils
from sky.utils import subprocess_utils

# Use the explicit logger name so that the logger is under the
# `sky.spot.controller` namespace when executed directly, so as
# to inherit the setup from the `sky` logger.
logger = sky_logging.init_logger('sky.spot.controller')


def _get_task_and_name(task_yaml: str) -> Tuple['sky.Task', str]:
    task = sky.Task.from_yaml(task_yaml)
    assert task.name is not None, task
    return task, task.name


class SpotController:
    """Each spot controller manages the life cycle of one spot cluster (job)."""

    def __init__(self, job_id: int, task_yaml: str,
                 retry_until_up: bool) -> None:
        self._job_id = job_id
        self._task, self._task_name = _get_task_and_name(task_yaml)

        self._retry_until_up = retry_until_up
        # TODO(zhwu): this assumes the specific backend.
        self._backend = cloud_vm_ray_backend.CloudVmRayBackend()

        # Add a unique identifier to the task environment variables, so that
        # the user can have the same id for multiple recoveries.
        #   Example value: sky-2022-10-04-22-46-52-467694_id-17
        task_envs = self._task.envs or {}
        job_id_env_var = common_utils.get_global_job_id(
            self._backend.run_timestamp, 'spot', str(self._job_id))
        task_envs[constants.JOB_ID_ENV_VAR] = job_id_env_var
        self._task.update_envs(task_envs)

        spot_state.set_submitted(
            self._job_id,
            self._task_name,
            self._backend.run_timestamp,
            resources_str=backend_utils.get_task_resources_str(self._task))
        logger.info(f'Submitted spot job; SKYPILOT_JOB_ID: {job_id_env_var}')
        self._cluster_name = spot_utils.generate_spot_cluster_name(
            self._task_name, self._job_id)
        self._strategy_executor = recovery_strategy.StrategyExecutor.make(
            self._cluster_name, self._backend, self._task, retry_until_up)

    def _run(self):
        """Busy loop monitoring spot cluster status and handling recovery.

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
        logger.info(f'Started monitoring spot job {self._job_id}, '
                    f'name: {self._task_name!r}.')
        spot_state.set_starting(self._job_id)
        job_submitted_at = self._strategy_executor.launch()

        spot_state.set_started(self._job_id, start_time=job_submitted_at)
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
            job_status = spot_utils.get_job_status(self._backend,
                                                   self._cluster_name)

            if job_status == job_lib.JobStatus.SUCCEEDED:
                end_time = spot_utils.get_job_timestamp(self._backend,
                                                        self._cluster_name,
                                                        get_end_time=True)
                # The job is done.
                spot_state.set_succeeded(self._job_id, end_time=end_time)
                break

            # For single-node jobs, nonterminated job_status indicates a
            # healthy cluster. We can safely continue monitoring.
            # For multi-node jobs, since the job may not be set to FAILED
            # immediately (depending on user program) when only some of the
            # nodes are preempted, need to check the actual cluster status.
            if (job_status is not None and not job_status.is_terminal() and
                    self._task.num_nodes == 1):
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
                 self._cluster_name, force_refresh=True)

            if cluster_status != global_user_state.ClusterStatus.UP:
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
                                                            self._cluster_name,
                                                            get_end_time=True)
                    logger.info(
                        'The user job failed. Please check the logs below.\n'
                        f'== Logs of the user job (ID: {self._job_id}) ==\n')
                    # TODO(zhwu): Download the logs, and stream them from the
                    # local disk, instead of streaming them from the spot
                    # cluster, to make it faster and more reliable.
                    returncode = self._backend.tail_logs(
                        handle, None, spot_job_id=self._job_id)
                    logger.info(f'\n== End of logs (ID: {self._job_id}, '
                                f'tail_logs returncode: {returncode}) ==')
                    spot_status_to_set = spot_state.SpotStatus.FAILED
                    if job_status == job_lib.JobStatus.FAILED_SETUP:
                        spot_status_to_set = spot_state.SpotStatus.FAILED_SETUP
                    failure_reason = (
                        'To see the details, run: '
                        f'sky spot logs --controller {self._job_id}')

                    spot_state.set_failed(self._job_id,
                                          failure_type=spot_status_to_set,
                                          failure_reason=failure_reason,
                                          end_time=end_time)
                    break
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
                    recovery_strategy.terminate_cluster(self._cluster_name)

            # Try to recover the spot jobs, when the cluster is preempted
            # or the job status is failed to be fetched.
            spot_state.set_recovering(self._job_id)
            recovered_time = self._strategy_executor.recover()
            spot_state.set_recovered(self._job_id,
                                     recovered_time=recovered_time)

    def run(self):
        """Run controller logic and handle exceptions."""
        try:
            self._run()
        except exceptions.ProvisionPrechecksError as e:
            # Please refer to the docstring of self._run for the cases when
            # this exception can occur.
            failure_reason = ('; '.join(
                common_utils.format_exception(reason, use_bracket=True)
                for reason in e.reasons))
            logger.error(failure_reason)
            spot_state.set_failed(
                self._job_id,
                failure_type=spot_state.SpotStatus.FAILED_PRECHECKS,
                failure_reason=failure_reason)
        except exceptions.SpotJobReachedMaxRetriesError as e:
            # Please refer to the docstring of self._run for the cases when
            # this exception can occur.
            logger.error(common_utils.format_exception(e))
            # The spot job should be marked as FAILED_NO_RESOURCE, as the
            # spot job may be able to launch next time.
            spot_state.set_failed(
                self._job_id,
                failure_type=spot_state.SpotStatus.FAILED_NO_RESOURCE,
                failure_reason=common_utils.format_exception(e))
        except (Exception, SystemExit) as e:  # pylint: disable=broad-except
            logger.error(traceback.format_exc())
            msg = ('Unexpected error occurred: '
                   f'{common_utils.format_exception(e, use_bracket=True)}')
            logger.error(msg)
            spot_state.set_failed(
                self._job_id,
                failure_type=spot_state.SpotStatus.FAILED_CONTROLLER,
                failure_reason=msg)


def _run_controller(job_id: int, task_yaml: str, retry_until_up: bool):
    """Runs the controller in a remote process for interruption."""
    # The controller needs to be instantiated in the remote process, since
    # the controller is not serializable.
    spot_controller = SpotController(job_id, task_yaml, retry_until_up)
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
            with signal_file.open(mode='r') as f:
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


def _cleanup(job_id: int, task_yaml: str):
    # NOTE: The code to get cluster name is same as what we did in the spot
    # controller, we should keep it in sync with SpotController.__init__()
    task, task_name = _get_task_and_name(task_yaml)
    cluster_name = spot_utils.generate_spot_cluster_name(task_name, job_id)
    recovery_strategy.terminate_cluster(cluster_name)
    # Clean up Storages with persistent=False.
    # TODO(zhwu): this assumes the specific backend.
    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    backend.teardown_ephemeral_storage(task)


def start(job_id, task_yaml, retry_until_up):
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
                                                     args=(job_id, task_yaml,
                                                           retry_until_up))
        controller_process.start()
        while controller_process.is_alive():
            _handle_signal(job_id)
            time.sleep(1)
    except exceptions.SpotUserCancelledError:
        logger.info(f'Cancelling spot job {job_id}...')
        spot_state.set_cancelling(job_id)
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

        logger.info(f'Cleaning up spot cluster of job {job_id}.')
        # NOTE: Originally, we send an interruption signal to the controller
        # process and the controller process handles cleanup. However, we
        # figure out the behavior differs from cloud to cloud
        # (e.g., GCP ignores 'SIGINT'). A possible explanation is
        # https://unix.stackexchange.com/questions/356408/strange-problem-with-trap-and-sigint
        # But anyway, a clean solution is killing the controller process
        # directly, and then cleanup the cluster state.
        _cleanup(job_id, task_yaml=task_yaml)
        logger.info(f'Spot cluster of job {job_id} has been taken down.')

        if cancelling:
            spot_state.set_cancelled(job_id)

        # We should check job status after 'set_cancelled', otherwise
        # the job status is not terminal.
        job_status = spot_state.get_status(job_id)
        # The job can be non-terminal if the controller exited abnormally,
        # e.g. failed to launch cluster after reaching the MAX_RETRY.
        assert job_status is not None
        if not job_status.is_terminal():
            logger.info(f'Previous spot job status: {job_status.value}')
            spot_state.set_failed(
                job_id,
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
    parser.add_argument('task_yaml',
                        type=str,
                        help='The path to the user spot task yaml file.')
    args = parser.parse_args()
    # We start process with 'spawn', because 'fork' could result in weird
    # behaviors; 'spawn' is also cross-platform.
    multiprocessing.set_start_method('spawn', force=True)
    start(args.job_id, args.task_yaml, args.retry_until_up)
