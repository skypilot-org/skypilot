"""AWS cloud adaptors"""

# pylint: disable=import-outside-toplevel


def client(service_name: str, **kwargs):
    """Create an AWS client of a certain service.

    Args:
        service_name: AWS service name (e.g., 's3', 'ec2').
        kwargs: Other options.
    """
    import boto3
    return boto3.client(service_name, **kwargs)


def resource(resource_name: str, **kwargs):
    """Create an AWS resource.

    Args:
        resource_name: AWS resource name (e.g., 's3').
        kwargs: Other options.
    """
    import boto3
    return boto3.resource(resource_name, **kwargs)


def session():
    """Create an AWS session."""
    import boto3
    return boto3.Session()


def client_exception():
    """Client exception."""
    from botocore import exceptions
    return exceptions.ClientError
