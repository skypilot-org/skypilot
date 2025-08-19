"""Utility functions for managed jobs."""
from sky import backends
from sky import sky_logging
from sky.backends import backend_utils
from sky.jobs import utils as managed_job_utils
from sky.skylet import constants as skylet_constants
from sky.utils import controller_utils

logger = sky_logging.init_logger(__name__)


def check_version_mismatch_and_non_terminal_jobs() -> None:
    """Check if controller has version mismatch and non-terminal jobs exist.

    Raises:
        ValueError: If there's a version mismatch and non-terminal jobs exist.
        sky.exceptions.ClusterNotUpError: If the controller is not accessible.
    """
    # Get the current local SKYLET_VERSION
    local_version = skylet_constants.SKYLET_VERSION

    # Get controller handle (works the same in both normal and
    # consolidation mode)
    jobs_controller_type = controller_utils.Controllers.JOBS_CONTROLLER
    handle = backend_utils.is_controller_accessible(
        controller=jobs_controller_type,
        stopped_message='Jobs controller is not running.')

    backend = backend_utils.get_backend_from_handle(handle)
    assert isinstance(backend, backends.CloudVmRayBackend)

    # Get controller version and raw job table
    code = managed_job_utils.ManagedJobCodeGen.get_version_and_job_table()

    returncode, output, stderr = backend.run_on_head(handle,
                                                     code,
                                                     require_outputs=True,
                                                     stream_logs=False,
                                                     separate_stderr=True)

    if returncode != 0:
        logger.error(output + stderr)
        raise ValueError('Failed to check controller version and jobs with '
                         f'returncode: {returncode}.\n{output + stderr}')

    # Parse the output to extract controller version (split only on first
    # newline)
    output_parts = output.strip().split('\n', 1)

    # Extract controller version from first line
    if len(output_parts) < 2 or not output_parts[0].startswith(
            'controller_version:'):
        raise ValueError(
            f'Expected controller version in first line, got: {output}')

    controller_version = output_parts[0].split(':', 1)[1]

    # Rest is job table payload (preserving any newlines within it)
    job_table_payload = output_parts[1]

    # Process locally: check version match and filter non-terminal jobs
    version_matches = controller_version == local_version

    # Load and filter jobs locally using existing method
    jobs, _, _, _, _ = managed_job_utils.load_managed_job_queue(
        job_table_payload)
    non_terminal_jobs = [job for job in jobs if not job['status'].is_terminal()]
    has_non_terminal_jobs = len(non_terminal_jobs) > 0

    if not version_matches and has_non_terminal_jobs:
        # Format job table locally using the same method as queue()
        formatted_job_table = managed_job_utils.format_job_table(
            non_terminal_jobs, show_all=False, show_user=False)

        error_msg = (
            f'Controller SKYLET_VERSION ({controller_version}) does not match '
            f'current version ({local_version}), and there are non-terminal '
            'jobs on the controller. Please wait for all jobs to complete or '
            'cancel them before launching new jobs with the updated version.'
            f'\n\nCurrent non-terminal jobs:\n{formatted_job_table}')

        raise ValueError(error_msg)
