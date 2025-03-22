import logging
import os
import socket
import subprocess

IMAGE_NAME = 'sky-remote-test-image'
CONTAINER_NAME = 'sky-remote-test'


def is_inside_docker() -> bool:
    """Check if the current environment is running inside a Docker container."""
    if os.path.exists('/.dockerenv'):
        return True

    return False


if is_inside_docker():
    # Buildkite have this env variable set to better identify the test container
    container_name_env = os.environ.get('CONTAINER_NAME')
    if container_name_env:
        CONTAINER_NAME += f'-{container_name_env}'


def get_api_server_endpoint_inside_docker() -> str:
    """Get the API server endpoint inside a Docker container."""
    host = 'host.docker.internal' if is_inside_docker() else '0.0.0.0'
    return f'http://{host}:46581'


def get_current_container_id() -> str:
    """Get the ID of the current Docker container."""
    assert is_inside_docker()
    return socket.gethostname()


def create_and_setup_new_container(target_container_name: str, host_port: int,
                                   container_port: int, username: str) -> str:
    """Create a new Docker container and copy files/directories from current container.

    Args:
        target_container_name: Name for the new container (default: sky-remote-test-buildkite-generic5)
        host_port: Port on host machine to bind (default: 46581)
        container_port: Port in container to expose (default: 46580)
        username: Username in the container (default: buildkite)

    Returns:
        ID of the newly created container
    """
    logger = logging.getLogger(__name__)

    # Common path definitions
    workspace_path = os.path.abspath(
        os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
    user_home = os.path.expanduser("~")

    # Define all the paths that should be copied/mounted
    src_dst_paths = {
        workspace_path: '/skypilot',
        os.path.join(user_home, '.aws'): f'/home/{username}/.aws',
        os.path.join(user_home, '.azure'): f'/home/{username}/.azure',
        os.path.join(user_home, '.config/gcloud'): f'/home/{username}/.config/gcloud',
        os.path.join(user_home, '.kube/config'): f'/home/{username}/.kube/config',
    }

    if is_inside_docker():
        # Get current container ID
        current_container_id = get_current_container_id()

        # Create the new container
        create_cmd = (f'docker create '
                      f'--name {target_container_name} '
                      f'-p {host_port}:{container_port} '
                      f'-e USERNAME={username} '
                      f'-e SKYPILOT_DISABLE_USAGE_COLLECTION=1 '
                      f'{IMAGE_NAME}')

        subprocess.check_call(create_cmd, shell=True)

        # Copy directories and files from current container to the new one
        for src_path, dst_path in src_dst_paths.items():
            # Check if it's a directory or file
            if os.path.isdir(src_path):
                if os.path.exists(src_path):
                    mkdir_cmd = f'docker exec {target_container_name} mkdir -p {dst_path}'
                    subprocess.check_call(mkdir_cmd, shell=True)

                    copy_dir_cmd = (
                        f'docker exec {current_container_id} tar -cf - -C {src_path} . | '
                        f'docker cp - {target_container_name}:{dst_path}')
                    subprocess.check_call(copy_dir_cmd, shell=True)
                else:
                    logger.warning(
                        f"Directory {src_path} does not exist, skipping copy")
            elif os.path.isfile(src_path):
                if os.path.exists(src_path):
                    copy_file_cmd = (
                        f'docker exec {current_container_id} cat {src_path} | '
                        f'docker cp - {target_container_name}:{dst_path}')
                    subprocess.check_call(copy_file_cmd, shell=True)
                else:
                    logger.warning(
                        f"File {src_path} does not exist, skipping copy")

        # Start the new container
        subprocess.check_call(f'docker start {target_container_name}',
                              shell=True)
    else:
        # Prepare volume mounts with read-write mode
        volumes = []
        for src_path, dst_path in src_dst_paths.items():
            if os.path.exists(src_path):
                volumes.append(f"{src_path}:{dst_path}")
            else:
                logger.warning(
                    f"Path {src_path} does not exist, skipping mount")

        # Run the container directly
        docker_cmd = [
            'docker',
            'run',
            '-d',
            '--name',
            target_container_name,
        ]

        # TODO(zeping): remove this once we have a platform-independent Docker file
        docker_cmd.extend(['--platform', 'linux/amd64'])

        docker_cmd.extend([
            *[f'-v={v}' for v in volumes], '-e', f'USERNAME={username}', '-e',
            'SKYPILOT_DISABLE_USAGE_COLLECTION=1', '-p',
            f'{host_port}:{container_port}', IMAGE_NAME
        ])

        subprocess.check_call(docker_cmd)

    # Get and return the new container ID
    new_container_id = subprocess.check_output(
        f'docker inspect --format="{{{{.Id}}}}" {target_container_name}',
        shell=True).decode().strip()

    return new_container_id
