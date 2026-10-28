import hashlib
import logging
import os
import subprocess
from typing import Dict, List, Optional, Tuple

IMAGE_NAME = 'sky-remote-test-image'
CONTAINER_NAME_PREFIX = 'sky-remote-test'

# Ports the API server and the metrics server listen on inside the container.
API_SERVER_CONTAINER_PORT = 46580
METRICS_CONTAINER_PORT = 9090

# Host ports are picked deterministically from [40000, 50000).
_HOST_PORT_RANGE_START = 40000
_HOST_PORT_RANGE_SIZE = 10000
# How many candidate host ports to try before giving up.
_MAX_HOST_PORT_ATTEMPTS = 10

# Docker's wording when it cannot bind a published host port.
_PORT_UNAVAILABLE_MARKERS = (
    'port is already allocated',
    'address already in use',
)

# Cache of (container name, container port) -> published host port. Looking the
# port up shells out to `docker port`, and callers ask for it once per generated
# test command.
_published_host_ports: Dict[Tuple[str, int], int] = {}


class PortUnavailableError(Exception):
    """Docker could not bind one of the requested host ports."""


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


def _candidate_host_port(container_name: str, attempt: int) -> int:
    """Get the `attempt`-th candidate host port for `container_name`.

    The port is derived from the container name so that every process in a test
    session agrees on it without coordinating. The range overlaps the kernel's
    ephemeral port range, though, so a candidate may already be taken by an
    unrelated process on the host -- a `kind` cluster publishing its API server,
    or just a transient outbound connection. `attempt` walks to the next
    candidate when that happens; attempt 0 keeps the historical port.
    """
    key = container_name if attempt == 0 else f'{container_name}:{attempt}'
    # Create a deterministic hash using MD5
    md5_hash = hashlib.md5(key.encode('utf-8')).hexdigest()
    # Convert first 8 chars of hash to integer and map to the port range.
    return _HOST_PORT_RANGE_START + (int(md5_hash[:8], 16) %
                                     _HOST_PORT_RANGE_SIZE)


def _published_host_port(container_name: str,
                         container_port: int) -> Optional[int]:
    """Get the host port Docker actually published for `container_port`.

    Returns None if the container does not exist or is not publishing the port.
    """
    cached = _published_host_ports.get((container_name, container_port))
    if cached is not None:
        return cached
    try:
        output = subprocess.check_output(
            ['docker', 'port', container_name, f'{container_port}/tcp'],
            stderr=subprocess.DEVNULL).decode()
    except (subprocess.CalledProcessError, OSError):
        return None
    # Output looks like '0.0.0.0:43649\n[::]:43649\n'.
    for binding in output.split():
        _, _, port = binding.rpartition(':')
        if port.isdigit():
            _published_host_ports[(container_name, container_port)] = int(port)
            return int(port)
    return None


def get_api_server_host_port() -> int:
    """Get the host port that will be listened by the test container.

    Prefers the port Docker actually published, so that every process -- the
    pytest-xdist workers included -- agrees on it even when container creation
    had to fall back to a different candidate port.
    """
    container_name = get_container_name()
    published = _published_host_port(container_name, API_SERVER_CONTAINER_PORT)
    if published is not None:
        return published
    return _candidate_host_port(container_name, 0)


def get_metrics_host_port() -> int:
    """Get the host port that will be mapped to the metrics server inside the test container."""
    container_name = get_container_name()
    published = _published_host_port(container_name, METRICS_CONTAINER_PORT)
    if published is not None:
        return published
    return _candidate_host_port(container_name, 0) + 1


def get_api_server_endpoint_inside_docker() -> str:
    """Get the API server endpoint inside a Docker container."""
    host = 'host.docker.internal' if is_inside_docker() else '0.0.0.0'
    return f'http://{host}:{get_api_server_host_port()}'


def get_metrics_endpoint_inside_docker() -> str:
    """Get the metrics server endpoint inside a Docker container."""
    host = 'host.docker.internal' if is_inside_docker() else '0.0.0.0'
    metrics_port = get_metrics_host_port()
    return f'http://{host}:{metrics_port}'


def _run_container(target_container_name: str, api_server_host_port: int,
                   api_server_container_port: int, metrics_host_port: int,
                   metrics_container_port: int, username: str,
                   volumes: List[str]) -> None:
    """Start the test container, publishing the given host ports.

    Raises:
        PortUnavailableError: one of the host ports is already taken.
        subprocess.CalledProcessError: `docker run` failed for any other reason.
    """
    if is_inside_docker():
        # Files are copied in afterwards rather than mounted, because the paths
        # are inside this container rather than on the host.
        run_cmd = [
            'docker', 'run', '-d', '--cpus=4', '--name', target_container_name,
            '-p', f'{api_server_host_port}:{api_server_container_port}', '-p',
            f'{metrics_host_port}:{metrics_container_port}',
            '--add-host=host.docker.internal:host-gateway', '-e',
            f'USERNAME={username}', '-e', 'LAUNCHED_BY_DOCKER_CONTAINER=1',
            '-e', 'SKYPILOT_DISABLE_USAGE_COLLECTION=1', '-e',
            'SKY_API_SERVER_METRICS_ENABLED=true', IMAGE_NAME
        ]
    else:
        run_cmd = [
            'docker', 'run', '-d', '--name', target_container_name,
            *[f'-v={v}' for v in volumes], '-e', f'USERNAME={username}', '-e',
            'SKYPILOT_DISABLE_USAGE_COLLECTION=1', '-e',
            'SKY_API_SERVER_METRICS_ENABLED=true', '-p',
            f'{api_server_host_port}:{api_server_container_port}', '-p',
            f'{metrics_host_port}:{metrics_container_port}', IMAGE_NAME
        ]

    proc = subprocess.run(run_cmd, capture_output=True, text=True, check=False)
    if proc.returncode == 0:
        return

    # A `docker run -d` that fails while publishing ports still leaves the
    # named container behind in `created` state, which would turn the next
    # attempt into a name conflict.
    subprocess.run(['docker', 'rm', '-f', target_container_name],
                   capture_output=True,
                   check=False)

    stderr = proc.stderr or ''
    if any(marker in stderr.lower() for marker in _PORT_UNAVAILABLE_MARKERS):
        raise PortUnavailableError(stderr.strip())
    raise subprocess.CalledProcessError(proc.returncode,
                                        run_cmd,
                                        output=proc.stdout,
                                        stderr=stderr)


def create_and_setup_new_container(target_container_name: str,
                                   api_server_container_port: int,
                                   metrics_container_port: int,
                                   username: str) -> str:
    """Create a new Docker container and copy files/directories from current container.

    The host ports are derived from the container name. They may collide with
    an unrelated process on the host, in which case the next candidate pair is
    tried; `get_api_server_host_port` reads the resulting port back from Docker.

    Args:
        target_container_name: Name for the new container
        api_server_container_port: Port in container to expose
        metrics_container_port: Port in container to expose
        username: Username in the container

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

    # Prepare volume mounts with read-write mode. Only used when running on the
    # host; inside Docker the files are copied in after the container starts.
    volumes = []
    if not is_inside_docker():
        for src_path, dst_path in src_dst_paths.items():
            if os.path.exists(src_path):
                volumes.append(f"{src_path}:{dst_path}")
            else:
                logger.warning(
                    f"Path {src_path} does not exist, skipping mount")

    for attempt in range(_MAX_HOST_PORT_ATTEMPTS):
        api_server_host_port = _candidate_host_port(target_container_name,
                                                    attempt)
        metrics_host_port = api_server_host_port + 1
        try:
            _run_container(target_container_name, api_server_host_port,
                           api_server_container_port, metrics_host_port,
                           metrics_container_port, username, volumes)
            break
        except PortUnavailableError as e:
            # The port range overlaps the kernel's ephemeral range, so an
            # unrelated process on the host may be sitting on the port we
            # picked. Move on to the next candidate instead of failing the
            # whole session.
            logger.warning(
                f'Host ports {api_server_host_port}/{metrics_host_port} are '
                f'unavailable for container {target_container_name} ({e}); '
                f'trying the next candidate ports.')
    else:
        raise RuntimeError(
            f'Could not find free host ports for container '
            f'{target_container_name} after {_MAX_HOST_PORT_ATTEMPTS} '
            f'attempts.')

    logger.info(f'Container {target_container_name} published API server on '
                f'host port {api_server_host_port} and metrics on '
                f'{metrics_host_port}')

    if is_inside_docker():
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

    # Get and return the new container ID
    new_container_id = subprocess.check_output(
        f'docker inspect --format="{{{{.Id}}}}" {target_container_name}',
        shell=True).decode().strip()

    return new_container_id
