import os


def _is_inside_docker() -> bool:
    """Check if the current environment is running inside a Docker container."""
    if os.path.exists('/.dockerenv'):
        return True

    return False


def get_api_server_endpoint_inside_docker() -> str:
    """Get the API server endpoint inside a Docker container."""
    host = 'host.docker.internal' if _is_inside_docker() else '0.0.0.0'
    return f'http://{host}:46581'
