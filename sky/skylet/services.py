"""gRPC service implementations for skylet."""

import os

import grpc

from sky import exceptions
from sky import sky_logging
from sky.schemas.generated import autostopv1_pb2
from sky.schemas.generated import autostopv1_pb2_grpc
from sky.schemas.generated import jobsv1_pb2
from sky.schemas.generated import jobsv1_pb2_grpc
from sky.skylet import autostop_lib
from sky.skylet import constants
from sky.skylet import job_lib
from sky.skylet import log_lib

logger = sky_logging.init_logger(__name__)


class AutostopServiceImpl(autostopv1_pb2_grpc.AutostopServiceServicer):
    """Implementation of the AutostopService gRPC service."""

    def SetAutostop(  # type: ignore[return]
            self, request: autostopv1_pb2.SetAutostopRequest,
            context: grpc.ServicerContext
    ) -> autostopv1_pb2.SetAutostopResponse:
        """Sets autostop configuration for the cluster."""
        try:
            wait_for = autostop_lib.AutostopWaitFor.from_protobuf(
                request.wait_for)
            autostop_lib.set_autostop(
                idle_minutes=request.idle_minutes,
                backend=request.backend,
                wait_for=wait_for if wait_for is not None else
                autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                down=request.down)
            return autostopv1_pb2.SetAutostopResponse()
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def IsAutostopping(  # type: ignore[return]
            self, request: autostopv1_pb2.IsAutostoppingRequest,
            context: grpc.ServicerContext
    ) -> autostopv1_pb2.IsAutostoppingResponse:
        """Checks if the cluster is currently autostopping."""
        try:
            is_autostopping = autostop_lib.get_is_autostopping()
            return autostopv1_pb2.IsAutostoppingResponse(
                is_autostopping=is_autostopping)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))


class JobsServiceImpl(jobsv1_pb2_grpc.JobsServiceServicer):
    """Implementation of the JobsService gRPC service."""

    def AddJob(  # type: ignore[return]
            self, request: jobsv1_pb2.AddJobRequest,
            context: grpc.ServicerContext) -> jobsv1_pb2.AddJobResponse:
        try:
            job_name = request.job_name if request.HasField('job_name') else '-'
            job_id, log_dir = job_lib.add_job(job_name, request.username,
                                              request.run_timestamp,
                                              request.resources_str,
                                              request.metadata)
            return jobsv1_pb2.AddJobResponse(job_id=job_id, log_dir=log_dir)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def QueueJob(  # type: ignore[return]
            self, request: jobsv1_pb2.QueueJobRequest,
            context: grpc.ServicerContext) -> jobsv1_pb2.QueueJobResponse:
        try:
            job_lib.scheduler.queue(request.job_id, request.cmd)
            return jobsv1_pb2.QueueJobResponse()
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def UpdateStatus(  # type: ignore[return]
            self, request: jobsv1_pb2.UpdateStatusRequest,
            context: grpc.ServicerContext) -> jobsv1_pb2.UpdateStatusResponse:
        try:
            job_lib.update_status()
            return jobsv1_pb2.UpdateStatusResponse()
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def GetJobQueue(  # type: ignore[return]
            self, request: jobsv1_pb2.GetJobQueueRequest,
            context: grpc.ServicerContext) -> jobsv1_pb2.GetJobQueueResponse:
        try:
            user_hash = request.user_hash if request.HasField(
                'user_hash') else None
            all_jobs = request.all_jobs
            jobs_info = job_lib.get_jobs_info(user_hash=user_hash,
                                              all_jobs=all_jobs)
            return jobsv1_pb2.GetJobQueueResponse(jobs=jobs_info)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def CancelJobs(  # type: ignore[return]
            self, request: jobsv1_pb2.CancelJobsRequest,
            context: grpc.ServicerContext) -> jobsv1_pb2.CancelJobsResponse:
        try:
            job_ids = list(request.job_ids) if request.job_ids else []
            user_hash = request.user_hash if request.HasField(
                'user_hash') else None
            cancelled_job_ids = job_lib.cancel_jobs(job_ids, request.cancel_all,
                                                    user_hash)
            return jobsv1_pb2.CancelJobsResponse(
                cancelled_job_ids=cancelled_job_ids)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def FailAllJobsInProgress(  # type: ignore[return]
        self, _: jobsv1_pb2.FailAllInProgressJobsRequest,
        context: grpc.ServicerContext
    ) -> jobsv1_pb2.FailAllInProgressJobsResponse:
        try:
            job_lib.fail_all_jobs_in_progress()
            return jobsv1_pb2.FailAllInProgressJobsResponse()
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def TailLogs(
            self,
            request: jobsv1_pb2.TailLogsRequest,  # type: ignore[return]
            context: grpc.ServicerContext):
        try:
            job_id = request.job_id if request.HasField(
                'job_id') else job_lib.get_latest_job_id()
            managed_job_id = request.managed_job_id if request.HasField(
                'managed_job_id') else None
            log_dir = job_lib.get_log_dir_for_job(job_id)
            if log_dir is None:
                run_timestamp = job_lib.get_run_timestamp(job_id)
                log_dir = None if run_timestamp is None else os.path.join(
                    constants.SKY_LOGS_DIRECTORY, run_timestamp)
            for line in log_lib.tail_logs_iter(job_id, log_dir, managed_job_id,
                                               request.follow, request.tail):
                yield jobsv1_pb2.TailLogsResponse(log_line=line)
            job_status = job_lib.get_status(job_id)
            exit_code = exceptions.JobExitCode.from_job_status(job_status)
            # Fix for dashboard: When follow=False and job is still running
            # (NOT_FINISHED=101), exit with success (0) since fetching current
            # logs is a successful operation.
            # This prevents shell wrappers from printing "command terminated
            # with exit code 101".
            exit_code_int = 0 if not request.follow and int(
                exit_code) == 101 else int(exit_code)
            yield jobsv1_pb2.TailLogsResponse(exit_code=exit_code_int)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def GetJobStatus(  # type: ignore[return]
            self, request: jobsv1_pb2.GetJobStatusRequest,
            context: grpc.ServicerContext) -> jobsv1_pb2.GetJobStatusResponse:
        try:
            job_ids = list(request.job_ids) if request.job_ids else [
                job_lib.get_latest_job_id()
            ]
            job_statuses = job_lib.get_statuses(job_ids)
            for job_id, status in job_statuses.items():
                job_statuses[job_id] = job_lib.JobStatus(status).to_protobuf(
                ) if status is not None else jobsv1_pb2.JOB_STATUS_UNSPECIFIED
            return jobsv1_pb2.GetJobStatusResponse(job_statuses=job_statuses)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def GetJobSubmittedTimestamp(  # type: ignore[return]
        self, request: jobsv1_pb2.GetJobSubmittedTimestampRequest,
        context: grpc.ServicerContext
    ) -> jobsv1_pb2.GetJobSubmittedTimestampResponse:
        try:
            timestamp = job_lib.get_job_submitted_or_ended_timestamp(
                request.job_id, False)
            if timestamp is None:
                context.abort(grpc.StatusCode.NOT_FOUND,
                              f'Job {request.job_id} not found')
            return jobsv1_pb2.GetJobSubmittedTimestampResponse(
                timestamp=timestamp)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def GetJobEndedTimestamp(  # type: ignore[return]
        self, request: jobsv1_pb2.GetJobEndedTimestampRequest,
        context: grpc.ServicerContext
    ) -> jobsv1_pb2.GetJobEndedTimestampResponse:
        try:
            timestamp = job_lib.get_job_submitted_or_ended_timestamp(
                request.job_id, True)
            if timestamp is None:
                context.abort(grpc.StatusCode.NOT_FOUND,
                              f'Job {request.job_id} not found or not ended')
            return jobsv1_pb2.GetJobEndedTimestampResponse(timestamp=timestamp)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def GetLogDirsForJobs(  # type: ignore[return]
            self, request: jobsv1_pb2.GetLogDirsForJobsRequest,
            context: grpc.ServicerContext
    ) -> jobsv1_pb2.GetLogDirsForJobsResponse:
        try:
            job_ids = list(request.job_ids)
            job_log_dirs = job_lib.get_job_log_dirs(job_ids)
            return jobsv1_pb2.GetLogDirsForJobsResponse(
                job_log_dirs=job_log_dirs)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))
