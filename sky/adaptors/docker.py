"""Docker adaptors"""

# pylint: disable=import-outside-toplevel

from functools import wraps

docker = None


def import_package(func):

    @wraps(func)
    def wrapper(*args, **kwargs):
        global docker
        if docker is None:
            try:
                import docker as _docker
            except ImportError:
                raise ImportError('Fail to import dependencies for Docker. '
                                  'See README for how to install it.') from None
            docker = _docker
        return func(*args, **kwargs)

    return wrapper


@import_package
def from_env():
    return docker.from_env()


@import_package
def build_error():
    return docker.errors.BuildError


@import_package
def not_found_error():
    return docker.errors.NotFound


@import_package
def api_error():
    return docker.errors.APIError
