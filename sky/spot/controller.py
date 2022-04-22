"""The controller module handles the life cycle of a sky spot cluster (job)."""

import time
from typing import Optional

from sky import global_user_state
from sky import sky_logging
from sky.spot import spot_status
from sky.backends import backend_utils
from sky.backends import cloud_vm_ray_backend
from sky.skylet import job_lib
from sky.spot.recovery_strategy import Strategy

logger = sky_logging.init_logger(__name__)

_JOB_STATUS_CHECK_GAP_SECONDS = 60


class SpotController:
    """Each spot controller manages the life cycle of one spot cluster (job)."""

    def __init__(self, cluster_name: str, task_yaml: str) -> None:
        self.cluster_name = cluster_name
        task_config = backend_utils.read_yaml(task_yaml)
        # TODO(zhwu): this assumes the specific backend.
        self.backend = cloud_vm_ray_backend.CloudVmRayBackend()

        self.strategy = Strategy.from_task_config(cluster_name, self.backend,
                                                  task_config)

    def _run(self):
        """Busy loop monitoring spot cluster status and handling recovery."""
        logger.info(f'Start monitoring spot cluster {self.cluster_name}')
        logger.info('Launching the spot cluster...')
        self.strategy.launch()
        spot_status.add_job(self.cluster_name, self.backend.run_timestamp)
        while True:
            # NOTE: we do not check cluster status first because race condition
            # can occur, i.e. cluster can be down during the job status check.
            # Refer to the design doc's Spot Controller Workflow section
            # https://docs.google.com/document/d/1vt6yGIK6wFYMkHC9HVTe_oISxPR90ugCliMXZKu762E/edit?usp=sharing # pylint: disable=line-too-long
            job_status = self._job_status_check()
            assert job_status != job_lib.JobStatus.INIT, (
                'Job status should not INIT')
            if job_status is not None and not job_status.is_terminal():
                # The job is normally running, continue to monitor the job status.
                time.sleep(_JOB_STATUS_CHECK_GAP_SECONDS)
                continue

            if job_status == job_lib.JobStatus.SUCCEEDED:
                # The job is done.
                break

            assert job_status is None or job_status == job_lib.JobStatus.FAILED, (
                f'The job should not be {job_status.value}.')
            if job_status == job_lib.JobStatus.FAILED:
                # Check the status of the spot cluster. It can be STOPPED or UP,
                # where STOPPED means partially down.
                cluster_status = backend_utils.get_cluster_status_with_refresh(
                    self.cluster_name, force_refresh=True)
                if cluster_status == global_user_state.ClusterStatus.UP:
                    # The user code has probably crashed.
                    break
                assert cluster_status == global_user_state.ClusterStatus.STOPPED, (
                    f'The cluster should be STOPPED, but is {cluster_status.value}.'
                )
            # Failed to connect to the cluster or the cluster is partially down.
            # job_status is None or job_status == job_lib.JobStatus.FAILED
            logger.info('The cluster is Preempted.')
            logger.info('=== Recovering... ===')
            self.strategy.recover()
            logger.info('==== Recovered. ====')
        return job_status

    def start(self):
        """Start the controller."""
        job_status = job_lib.JobStatus.FAILED
        try:
            job_status = self._run()
        finally:
            self.strategy.terminate()
            # TODO(zhwu): write the job_status

    def _job_status_check(self) -> Optional['job_lib.JobStatus']:
        """Check the status of the job running on the spot cluster.
        It can be INIT, RUNNING, SUCCEEDED, FAILED or CANCELLED."""
        handle = global_user_state.get_handle_from_cluster_name(
            self.cluster_name)
        status = None
        try:
            logger.info('=== Checking the job status... ===')
            status = self.backend.get_job_status(handle, stream_logs=False)
            logger.info(f'Job status: {status}')
        except SystemExit:
            # Fail to connect to the cluster
            logger.info('Fail to connect to the cluster.')
        logger.info('=' * 34)
        return status


if __name__ == '__main__':
    controller = SpotController('test-spot-controller',
                                'examples/spot_recovery.yaml')
    controller.start()
