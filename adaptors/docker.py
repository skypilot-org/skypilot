"""Docker adaptors"""

# pylint: disable=import-outside-toplevel

from sky.adaptors import common

docker = common.LazyImport(
    'docker',
    import_error_message='Failed to import dependencies for Docker. '
    'See README for how to install it.')


def from_env():
    return docker.from_env()


def build_error():
    return docker.errors.BuildError


def not_found_error():
    return docker.errors.NotFound


def api_error():
    return docker.errors.APIError
