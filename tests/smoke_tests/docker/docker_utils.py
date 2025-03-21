import os

IMAGE_NAME = 'sky-remote-test-image'
CONTAINER_NAME = 'sky-remote-test'


def _is_inside_docker() -> bool:
    """Check if the current environment is running inside a Docker container."""
    if os.path.exists('/.dockerenv'):
        return True

    return False


if _is_inside_docker():
    # Buildkite have this env variable set to better identify the test container
    container_name_env = os.environ.get('CONTAINER_NAME')
    if container_name_env:
        CONTAINER_NAME += f'-{container_name_env}'


def get_api_server_endpoint_inside_docker() -> str:
    """Get the API server endpoint inside a Docker container."""
    if _is_inside_docker():
        return f'http://{CONTAINER_NAME}:46581'
    else:
        return f'http://0.0.0.0:46581'
