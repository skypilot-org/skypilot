import hashlib
import logging
import os
import subprocess

IMAGE_NAME = 'sky-remote-test-image'
CONTAINER_NAME_PREFIX = 'sky-remote-test'


def is_inside_docker() -> bool:
    """Check if the current environment is running inside a Docker container."""
    if os.path.exists('/.dockerenv') or os.path.exists(
            '/proc/sys/fs/binfmt_misc/WSLInterop'):
        return True

    return False


def get_container_name() -> str:
    """Get the name of the container."""
    container_name = CONTAINER_NAME_PREFIX
    if is_inside_docker():
        # Buildkite have this env variable set to better identify the test container
        container_name_env = os.environ.get('CONTAINER_NAME')
        if container_name_env:
            container_name += f'-{container_name_env}'

    return container_name


def get_host_port() -> int:
    """Get the host port that will be listened by the test container."""
    container_name = get_container_name()
    # Create a deterministic hash using MD5
    md5_hash = hashlib.md5(container_name.encode('utf-8')).hexdigest()
    # Convert first 8 chars of hash to integer and map to port range 40000-50000
    port = 40000 + (int(md5_hash[:8], 16) % 10000)
    return port


def get_api_server_endpoint_inside_docker() -> str:
    """Get the API server endpoint inside a Docker container."""
    host = 'host.docker.internal' if is_inside_docker() else '0.0.0.0'
    return f'http://{host}:{get_host_port()}'


def get_metrics_endpoint_inside_docker() -> str:
    """Get the metrics server endpoint inside a Docker container."""
    host = 'host.docker.internal' if is_inside_docker() else '0.0.0.0'
    metrics_port = get_host_port() + 1  # Metrics port is host_port + 1
    return f'http://{host}:{metrics_port}'


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
        # Use this directory to check if all files are copied/mounted correctly.
        os.path.join(workspace_path, 'tests/smoke_tests/docker'): f'/success_mount_directory',
    }

    if is_inside_docker():
        # Run the new container directly
        # Also expose metrics port (9090 -> host_port + 1)
        metrics_host_port = host_port + 1
        run_cmd = (f'docker run -d '
                   f'--name {target_container_name} '
                   f'-p {host_port}:{container_port} '
                   f'-p {metrics_host_port}:9090 '
                   f'--add-host=host.docker.internal:host-gateway '
                   f'-e USERNAME={username} '
                   f'-e LAUNCHED_BY_DOCKER_CONTAINER=1 '
                   f'-e SKYPILOT_DISABLE_USAGE_COLLECTION=1 '
                   f'-e SKY_API_SERVER_METRICS_ENABLED=true '
                   f'{IMAGE_NAME}')

        subprocess.check_call(run_cmd, shell=True)

        # Copy directories and files from current container to the new one
        for src_path, dst_path in src_dst_paths.items():
            if os.path.exists(src_path):
                if os.path.isdir(src_path):
                    # Copy directory contents
                    # The "/." at the end copies the contents of the directory, not the directory itself
                    copy_cmd = (
                        f'docker cp {src_path}/. {target_container_name}:{dst_path} && '
                        f'docker exec {target_container_name} sudo chown -R {username} {dst_path}'
                    )
                elif os.path.isfile(src_path):
                    # Copy file
                    # First create the parent directory in the container
                    copy_cmd = (
                        f'docker exec {target_container_name} mkdir -p {os.path.dirname(dst_path)} && '
                        f'docker cp {src_path} {target_container_name}:{dst_path} && '
                        f'docker exec {target_container_name} sudo chown -R {username} {dst_path}'
                    )
                logger.info(f'Running copy command: {copy_cmd}')
                subprocess.check_call(copy_cmd, shell=True)
            else:
                logger.warning(f"Path {src_path} does not exist, skipping copy")
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

        # Also expose metrics port (9090 -> host_port + 1)
        metrics_host_port = host_port + 1
        docker_cmd.extend([
            *[f'-v={v}' for v in volumes], '-e', f'USERNAME={username}', '-e',
            'SKYPILOT_DISABLE_USAGE_COLLECTION=1', '-e',
            'SKY_API_SERVER_METRICS_ENABLED=true', '-p',
            f'{host_port}:{container_port}', '-p', f'{metrics_host_port}:9090',
            IMAGE_NAME
        ])

        subprocess.check_call(docker_cmd)

    # Get and return the new container ID
    new_container_id = subprocess.check_output(
        f'docker inspect --format="{{{{.Id}}}}" {target_container_name}',
        shell=True).decode().strip()

    return new_container_id
