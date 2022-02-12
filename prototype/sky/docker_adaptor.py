"""Docker adaptors"""

# pylint: disable=import-outside-toplevel


def from_env():
    import docker
    return docker.from_env()


def build_error():
    import docker
    return docker.errors.BuildError


def not_found_error():
    import docker
    return docker.errors.NotFound


def api_error():
    import docker
    return docker.errors.APIError
