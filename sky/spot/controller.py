"""The controller module handles the life cycle of a sky spot cluster (job)."""

import time

from sky import global_user_state
from sky.backends import backend_utils
from sky.skylet import job_lib
from sky.spot.recovery_strategy import Strategy

_JOB_STATUS_CHECK_GAP_SECONDS = 1


class SpotController:
    """Each spot controller manages the life cycle of one spot cluster (job)."""

    def __init__(self, task_yaml: str) -> None:
        task_config = backend_utils.read_yaml(task_yaml)
        recovery_strategy = task_config.pop('spot_recovery')
        self.strategy = Strategy.from_strategy(recovery_strategy, task_config)

    def run(self):
        """Busy loop monitoring spot cluster status and handling recovery."""
        self.strategy.initial_launch()
        job_status = job_lib.JobStatus.INIT
        while True:
            job_status = self._job_status_check()
            if not job_status.is_terminal():
                time.sleep(_JOB_STATUS_CHECK_GAP_SECONDS)
                continue

            if job_status == job_lib.JobStatus.SUCCEEDED:
                # Job succeeded, no need to relaunch
                break

            # Job is in abnormal terminal state, check the cluster status
            assert job_status != job_lib.JobStatus.CANCELLED, (
                'Job should not be cancelled in spot mode')
            if job_status == job_lib.JobStatus.FAILED:
                cluster_status = self._cluster_status_check()
                if cluster_status == global_user_state.ClusterStatus.UP:
                    # Cluster is up, indicating the job is failed due to
                    # the problem in user's code.
                    break

                self.strategy.recover()

        self._terminate()

    def _job_status_check(self) -> 'job_lib.JobStatus':
        """Check the status of the job running on the spot cluster.
        It can be INIT, RUNNING, SUCCEEDED, FAILED or CANCELLED."""
        raise NotImplementedError

    def _cluster_status_check(self) -> 'global_user_state.ClusterStatus':
        """Check the status of the spot cluster. It can be INIT or UP,
        where INIT means partially down."""
        raise NotImplementedError

    def _terminate(self):
        """Terminate the spot cluster."""
        raise NotImplementedError
