"""Utils for AWS provisioner."""
import threading
from typing import Any, Dict

import boto3
from botocore import config
import colorama
import urllib3

from sky import sky_logging

logger = sky_logging.init_logger(__name__)

BOTO_MAX_RETRIES = 12

# ======================== Thread-safe ========================
# https://boto3.amazonaws.com/v1/documentation/api/latest/guide/resources.html#multithreading-or-multiprocessing-with-resources

_lock = threading.RLock()


def create_resource(resource: str,
                    region: str,
                    max_attempts: int = BOTO_MAX_RETRIES,
                    **credentials) -> Any:
    """Create an AWS resource."""
    # overhead: 6.69 ms ± 41.5 µs
    with _lock:
        # We should prevent caching the resource for thread safety.
        # Instead, the logic that uses the resource should reuse the same
        # resource as much as possible.
        return boto3.resource(
            resource,
            region,
            config=config.Config(retries={'max_attempts': max_attempts}),
            **credentials)


def handle_boto_error(exc: Exception, msg: str, *args, **kwargs) -> None:
    """Handle boto3 error properly."""
    error_code = None
    error_info = None
    # todo: not sure if these exceptions always have response
    if hasattr(exc, 'response'):
        error_info = exc.response.get('Error', None)
    if error_info is not None:
        error_code = error_info.get('Code', None)

    generic_message = (f'{msg}\nError code: {colorama.Style.BRIGHT}{error_code}'
                       f'{colorama.Style.RESET_ALL}')

    # apparently
    # ExpiredTokenException
    # ExpiredToken
    # RequestExpired
    # are all the same pretty much
    credentials_expiration_codes = [
        'ExpiredTokenException',
        'ExpiredToken',
        'RequestExpired',
    ]

    if error_code in credentials_expiration_codes:
        # 'An error occurred (ExpiredToken) when calling the
        # GetInstanceProfile operation: The security token
        # included in the request is expired'

        # 'An error occurred (RequestExpired) when calling the
        # DescribeKeyPairs operation: Request has expired.'

        token_command = ('aws sts get-session-token '
                         '--serial-number arn:aws:iam::' + 'ROOT_ACCOUNT_ID' +
                         ':mfa/' + 'AWS_USERNAME' + ' --token-code ' +
                         'TWO_FACTOR_AUTH_CODE')

        secret_key_var = ('export AWS_SECRET_ACCESS_KEY = ' + 'REPLACE_ME' +
                          ' # found at Credentials.SecretAccessKey')
        session_token_var = ('export AWS_SESSION_TOKEN = ' + 'REPLACE_ME' +
                             ' # found at Credentials.SessionToken')
        access_key_id_var = ('export AWS_ACCESS_KEY_ID = ' + 'REPLACE_ME' +
                             ' # found at Credentials.AccessKeyId')

        # fixme: replace with a Github URL that points
        # to our repo
        aws_session_script_url = (
            'https://gist.github.com/maximsmol/a0284e1d97b25d417bd9ae02e5f450cf'
        )

        logger.error(generic_message)
        logger.debug(vars(exc))

        logger.fatal(
            'Your AWS session has expired.\n'
            f'You can request a new one using {colorama.Style.BRIGHT}'
            f'{token_command}{colorama.Style.RESET_ALL} then expose it '
            f'to SkyPilot by setting {colorama.Style.BRIGHT}'
            f'{secret_key_var} {session_token_var} {access_key_id_var}'
            f'{colorama.Style.RESET_ALL}\n'
            f'You can find a script that automates this at:'
            f'{aws_session_script_url}')
        # Do not re-raise the exception here because it looks awful
        # and we already print all the info in verbose
        raise SystemExit(1)

    # todo: any other errors that we should catch separately?

    logger.fatal(generic_message)
    logger.fatal('')
    logger.error(f'Boto3 error: {vars(exc)} {exec}')
    raise SystemExit(1)


def get_self_instance_metadata() -> Dict[str, str]:
    """Get instance metadata within an instance."""
    # https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/instancedata-data-retrieval.html
    prefix = 'http://169.254.169.254/latest/meta-data'
    http = urllib3.PoolManager()
    instance_req = http.request('GET', f'{prefix}/instance-id')
    region_req = http.request('GET', f'{prefix}/placement/region')
    availability_zone_req = http.request(
        'GET', f'{prefix}/placement/availability-zone')
    instance_id = instance_req.data.decode()
    region = region_req.data.decode()
    availability_zone = availability_zone_req.data.decode()

    if instance_req.status != 200:
        raise ValueError('Get "instance-id" from metadata with status '
                         f'{instance_req.status}. Message: {instance_id}')
    if region_req.status != 200:
        raise ValueError('Get "region" from metadata with status '
                         f'{region_req.status}. Message: {region}')
    if availability_zone_req.status != 200:
        raise ValueError(f'Get "availability_zone" from metadata with status '
                         f'{availability_zone_req.status}. Message: '
                         f'{availability_zone}')
    return {
        'instance_id': instance_id,
        'region': region,
        'availability_zone': availability_zone,
    }
