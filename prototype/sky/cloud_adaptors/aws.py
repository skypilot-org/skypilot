"""AWS cloud adaptors"""


# pylint: disable=import-outside-toplevel


def client(service_name: str, **kwargs):
    import boto3
    return boto3.client(service_name, **kwargs)


def resource(resource_name: str):
    import boto3
    return boto3.resource(resource_name)


def session():
    import boto3
    return boto3.Session()


def client_exception():
    from botocore import exceptions
    return exceptions.ClientError
