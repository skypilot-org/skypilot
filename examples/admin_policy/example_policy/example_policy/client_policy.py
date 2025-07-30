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

_LOCAL_GCP_CREDENTIALS_SET_ENV_VAR = 'SKYPILOT_LOCAL_GCP_CREDENTIALS_SET'
_POLICY_VERSION = 'v1'


class UseLocalGcpCredentialsPolicy(sky.AdminPolicy):
    """Example policy: use local GCP credentials in the task."""

    @classmethod
    def validate_and_mutate(
            cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
        # Only apply the policy at client-side.
        if not user_request.at_client_side:
            if not _LOCAL_GCP_CREDENTIALS_SET_ENV_VAR in user_request.task.envs:
                raise RuntimeError(
                    f'Policy {cls.__name__} was not applied at client-side. '
                    'Please install the policy and retry.')
            cv = user_request.task.envs[_LOCAL_GCP_CREDENTIALS_SET_ENV_VAR]
            if cv != _POLICY_VERSION:
                raise RuntimeError(
                    f'Policy {cls.__name__} at {cv} was applied at client-side '
                    f'but the server requires {_POLICY_VERSION} to be applied. '
                    'Please upgrade the policy and retry.')
            return sky.MutatedUserRequest(user_request.task,
                                          user_request.skypilot_config)

        task = user_request.task
        if task.file_mounts is None:
            task.set_file_mounts({})
        # Use the env var to detect whether an explicit credential path is
        # specified.
        cred_path = os.environ.get(_GOOGLE_APPLICATION_CREDENTIALS_ENV)

        task_file_mounts = task.file_mounts or {}
        if cred_path is not None:
            task_file_mounts[_GOOGLE_APPLICATION_CREDENTIALS_PATH] = cred_path
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
            task_file_mounts['~/.config/gcloud'] = '~/.config/gcloud'
        task.set_file_mounts(task_file_mounts)
        task.envs[_LOCAL_GCP_CREDENTIALS_SET_ENV_VAR] = _POLICY_VERSION
        return sky.MutatedUserRequest(task, user_request.skypilot_config)
