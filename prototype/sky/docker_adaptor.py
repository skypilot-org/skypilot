"""Docker adaptors"""

# pylint: disable=import-outside-toplevel

from functools import wraps

docker = None


def with_docker(func):

    @wraps(func)
    def wrapper(*args, **kwargs):
        global docker
        if docker is None:
            import docker as _docker
            docker = _docker
        return func(*args, **kwargs)

    return wrapper


@with_docker
def from_env():
    return docker.from_env()


@with_docker
def build_error():
    return docker.errors.BuildError


@with_docker
def not_found_error():
    return docker.errors.NotFound


@with_docker
def api_error():
    return docker.errors.APIError
