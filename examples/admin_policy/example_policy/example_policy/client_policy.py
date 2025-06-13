"""Example prebuilt admin policies for client usage specifically."""
import os

import sky
from sky import sky_logging

logger = sky_logging.init_logger(__name__)

# Ref:
# https://cloud.google.com/docs/authentication/provide-credentials-adc#local-key
_GOOGLE_APPLICATION_CREDENTIALS_ENV = 'GOOGLE_APPLICATION_CREDENTIALS'
_GOOGLE_APPLICATION_CREDENTIALS_PATH = (
    '~/.config/gcloud/application_default_credentials.json')


class UseLocalGcpCredentialsPolicy(sky.AdminPolicy):
    """Example policy: use local GCP credentials in the task."""

    @classmethod
    def validate_and_mutate(
            cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
        # Only apply the policy at client-side.
        if not user_request.at_client_side:
            return sky.MutatedUserRequest(user_request.task,
                                          user_request.skypilot_config)

        task = user_request.task
        if task.file_mounts is None:
            task.file_mounts = {}
        # Use the env var to detect whether an explicit credential path is
        # specified.
        cred_path = os.environ.get(_GOOGLE_APPLICATION_CREDENTIALS_ENV)

        if cred_path is not None:
            task.file_mounts[_GOOGLE_APPLICATION_CREDENTIALS_PATH] = cred_path
            activate_cmd = (f'gcloud auth activate-service-account --key-file '
                            f'{_GOOGLE_APPLICATION_CREDENTIALS_PATH}')
            if task.run is None:
                task.run = activate_cmd
            elif isinstance(task.run, str):
                task.run = f'{activate_cmd} && {task.run}'
            else:
                # Impossible according to current code base, but just in case.
                logger.warning('The task run command is not a string, '
                               f'so the local {cred_path} will not be used.')
        else:
            # Otherwise upload the entire default credential directory to get
            # consistent identity in the task and the local environment.
            task.file_mounts['~/.config/gcloud'] = '~/.config/gcloud'
        return sky.MutatedUserRequest(task, user_request.skypilot_config)
