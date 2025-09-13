"""Utils for AWS provisioner."""
import colorama

from sky import exceptions
from sky import sky_logging

logger = sky_logging.init_logger(__name__)

BOTO_MAX_RETRIES = 12

# ======================== Thread-safe ========================
# https://boto3.amazonaws.com/v1/documentation/api/latest/guide/resources.html#multithreading-or-multiprocessing-with-resources


def handle_boto_error(exc: Exception, msg: str) -> None:
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

    # For invalid credentials, like expired tokens or
    # invalid tokens, raise InvalidCloudCredentials exception
    invalid_credentials_codes = [
        'ExpiredTokenException',
        'ExpiredToken',
        'RequestExpired',
        'InvalidClientTokenId',
        'InvalidClientToken',
    ]

    if error_code in invalid_credentials_codes:
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
        logger.info(vars(exc))

        logger.fatal(
            'Your AWS session has expired.\n'
            f'You can request a new one using {colorama.Style.BRIGHT}'
            f'{token_command}{colorama.Style.RESET_ALL} then expose it '
            f'to SkyPilot by setting {colorama.Style.BRIGHT}'
            f'{secret_key_var} {session_token_var} {access_key_id_var}'
            f'{colorama.Style.RESET_ALL}\n'
            f'You can find a script that automates this at:'
            f'{aws_session_script_url}')
        # Raise the InvalidCloudCredentials exception so that
        # the provisioner can failover to other clouds
        raise exceptions.InvalidCloudCredentials(
            f'InvalidCloudCredentials: {generic_message}') from exc

    # todo: any other errors that we should catch separately?

    logger.fatal(generic_message)
    logger.fatal('')
    logger.error(f'Boto3 error: {vars(exc)} {exec}')
    raise SystemExit(1)
