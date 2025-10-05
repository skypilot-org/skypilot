"""Tests for AWS utils.

"""

import pytest

from sky import exceptions
from sky.adaptors import aws
from sky.provision.aws import utils


def test_handle_boto_error():
    # Create a mock error response
    error_response = {
        'Error': {
            'Code': 'ExpiredToken',
            'Message': 'Token expired',
            'Type': 'Sender'
        },
        'ResponseMetadata': {
            'RequestId': '123456-7890',
            'HTTPStatusCode': 400
        }
    }

    with pytest.raises(exceptions.InvalidCloudCredentials):
        utils.handle_boto_error(
            aws.botocore_exceptions().ClientError(error_response, 'test'),
            'test')
