"""Initialize docker containers on a remote node."""

import dataclasses
import shlex
import time
from typing import Any, Dict, List

from sky import sky_logging
from sky.skylet import constants
from sky.utils import command_runner
from sky.utils import subprocess_utils

logger = sky_logging.init_logger(__name__)

# Configure environment variables. A docker image can have environment variables
# set in the Dockerfile with `ENV``. We need to export these variables to the
# shell environment, so that our ssh session can access them.
SETUP_ENV_VARS_CMD = (
    'prefix_cmd() '
    '{ if [ $(id -u) -ne 0 ]; then echo "sudo"; else echo ""; fi; } && '
    'printenv | while IFS=\'=\' read -r key value; do echo "export $key=\\\"$value\\\""; done > '  # pylint: disable=line-too-long
    '~/container_env_var.sh && '
    '$(prefix_cmd) mv ~/container_env_var.sh /etc/profile.d/container_env_var.sh'
)

# Docker daemon may not be ready when the machine is firstly started. The error
# message starts with the following string. We should wait for a while and retry
# the command.
DOCKER_PERMISSION_DENIED_STR = ('permission denied while trying to connect to '
                                'the Docker daemon socket')
_DOCKER_SOCKET_WAIT_TIMEOUT_SECONDS = 30


@dataclasses.dataclass
class DockerLoginConfig:
    """Config for docker login. Used for pulling from private registries."""
    username: str
    password: str
    server: str

    @classmethod
    def from_env_vars(cls, d: Dict[str, str]) -> 'DockerLoginConfig':
        return cls(
            username=d[constants.DOCKER_USERNAME_ENV_VAR],
            password=d[constants.DOCKER_PASSWORD_ENV_VAR],
            server=d[constants.DOCKER_SERVER_ENV_VAR],
        )


# Copied from ray.autoscaler._private.ray_constants
# The default maximum number of bytes to allocate to the object store unless
# overridden by the user.
DEFAULT_OBJECT_STORE_MAX_MEMORY_BYTES = 200 * 10**9
# The default proportion of available memory allocated to the object store
DEFAULT_OBJECT_STORE_MEMORY_PROPORTION = 0.3


def docker_start_cmds(
    image,
    container_name,
    user_options,
    docker_cmd,
):
    """Generating docker start command.

    The code is borrowed from `ray.autoscaler._private.command_runner`.
    We made the following two changes:
      1. Remove `--rm` to keep the container after `ray stop` is executed.
      2. Remove mount options, as all the file mounts will be handled after
        the container is started, through `rsync` command.
    """

    # for click, used in ray cli
    env_vars = {'LC_ALL': 'C.UTF-8', 'LANG': 'C.UTF-8'}
    env_flags = ' '.join(
        ['-e {name}={val}'.format(name=k, val=v) for k, v in env_vars.items()])

    user_options_str = ' '.join(user_options)
    docker_run = [
        docker_cmd,
        'run',
        # SkyPilot: Remove --rm flag to keep the container after `ray stop`
        # is executed.
        '--name {}'.format(container_name),
        '-d',
        '-it',
        env_flags,
        user_options_str,
        '--net=host',
        # SkyPilot: Add following options to enable fuse.
        '--cap-add=SYS_ADMIN',
        '--device=/dev/fuse',
        '--security-opt=apparmor:unconfined',
        image,
        'bash',
    ]
    return ' '.join(docker_run)


def _with_interactive(cmd):
    force_interactive = (
        f'source ~/.bashrc; '
        f'export OMP_NUM_THREADS=1 PYTHONWARNINGS=ignore && ({cmd})')
    return ['bash', '--login', '-c', '-i', shlex.quote(force_interactive)]


# SkyPilot: New class to initialize docker containers on a remote node.
# Adopted from ray.autoscaler._private.command_runner.DockerCommandRunner.
class DockerInitializer:
    """Initializer for docker containers on a remote node."""

    def __init__(self, docker_config: Dict[str, Any],
                 runner: 'command_runner.CommandRunner', log_path: str):
        self.docker_config = docker_config
        self.container_name = docker_config['container_name']
        self.runner = runner
        self.home_dir = None
        self.initialized = False
        # podman is not fully tested yet.
        use_podman = docker_config.get('use_podman', False)
        self.docker_cmd = 'podman' if use_podman else 'docker'
        self.log_path = log_path

    def _run(self,
             cmd,
             run_env='host',
             wait_for_docker_daemon: bool = False,
             separate_stderr: bool = False,
             log_err_when_fail: bool = True) -> str:

        if run_env == 'docker':
            cmd = self._docker_expand_user(cmd, any_char=True)
            cmd = ' '.join(_with_interactive(cmd))
            # SkyPilot: We do not include `-it` flag here, as that will cause
            # an error: `the input device is not a TTY`, and it works without
            # `-it` flag.
            # TODO(zhwu): ray use the `-it` flag, we need to check why.
            cmd = (f'{self.docker_cmd} exec {self.container_name} /bin/bash -c'
                   f' {shlex.quote(cmd)} ')

        logger.debug(f'+ {cmd}')
        start = time.time()
        while True:
            rc, stdout, stderr = self.runner.run(
                cmd,
                require_outputs=True,
                stream_logs=False,
                separate_stderr=separate_stderr,
                log_path=self.log_path)
            if (DOCKER_PERMISSION_DENIED_STR in stdout + stderr and
                    wait_for_docker_daemon):
                if time.time() - start > _DOCKER_SOCKET_WAIT_TIMEOUT_SECONDS:
                    if rc == 0:
                        # Set returncode to 1 if failed to connect to docker
                        # daemon after timeout.
                        rc = 1
                    break
                # Close the cached connection to make the permission update of
                # ssh user take effect, e.g. usermod -aG docker $USER, called
                # by cloud-init of Azure.
                self.runner.close_cached_connection()
                logger.info('Failed to connect to docker daemon. It might be '
                            'initializing, retrying in 5 seconds...')
                time.sleep(5)
                continue
            break
        subprocess_utils.handle_returncode(
            rc,
            cmd,
            error_msg='Failed to run docker setup commands.',
            stderr=stdout + stderr,
            # Print out the error message if the command failed.
            stream_logs=log_err_when_fail)
        return stdout.strip()

    def _check_host_status(self) -> dict:
        result = self._run(f"""
                # passing initializer state
                _self_initialized={"1" if self.initialized else "0"}
                _docker_socket_wait_timeout_seconds={_DOCKER_SOCKET_WAIT_TIMEOUT_SECONDS}

                # get mem
                cat /proc/meminfo | grep MemAvailable || true

                # check if docker is installed
                docker_command_exist=$(command -v {self.docker_cmd})
                if [[ -n $docker_command_exist && $docker_command_exist == *"docker"* ]]
                then
                    docker_installed=1
                    echo "docker_installed:" "Y"
                else
                    docker_installed=0
                    echo "docker_installed:" "N"
                fi

                if [[ $docker_installed -eq 1 ]]
                then
                    # wait for docker daemon to be ready
                    docker_ready=0
                    end_time=$((SECONDS + _docker_socket_wait_timeout_seconds))
                    while [ $SECONDS -lt $end_time ]; do
                        if {self.docker_cmd} info >/dev/null 2>&1; then
                            echo "docker_ready:" "Y"
                            docker_ready=1
                            break
                        else
                            exec su -l $USER
                            sleep 5
                        fi
                    done
                    if [[ $docker_ready -eq 0 ]]
                    then
                        echo "docker_ready:" "N"
                    fi

                    # check runtime info
                    echo "docker_runtime:" $({self.docker_cmd} info -f "{{{{.Runtimes}}}}")

                    if [[ $_self_initialized -eq 0 ]]
                    then 
                        # check container status
                        container_status=$({self.docker_cmd} ps -a --filter name="{self.container_name}" --format "{{{{.Status}}}}")
                        echo "container_status:" $container_status
                        # if container is up, check its image
                        if [[ $container_status == *"Up"* ]]
                        then
                            container_image=$({self.docker_cmd} inspect --format='{{{{.Config.Image}}}}' {self.container_name})
                            echo "container_image:" $container_image
                        fi
                    fi 
                fi
                echo "status_checking_completed:" "Y"
            """)
        ret = {}

        for l in result.split("\n"):
            if ":" in l:
                x = l.split(":")
                k = x[0].strip()
                v = ":".join(x[1:]).strip()
                if v in ["Y", "N"]:
                    v = True if v == "Y" else False
                if k == "MemAvailable":
                    k = "mem_available_in_kb"
                    v = int(v.split()[0])
                ret[k] = v
        return ret

    def initialize(self) -> str:
        specific_image = self.docker_config['image']

        status = self._check_host_status()
        logger.info(f"Host status: {str(status)}")

        if "container_status" in status:
            if "Exited" in status["container_status"]:
                logger.info("Container is exited but not removed.")
                self.initialized = True
                self._run(f'{self.docker_cmd} start {self.container_name}')
                self._run('sudo service ssh start', run_env='docker')
                return self._run('whoami', run_env='docker')

        # SkyPilot: Docker login if user specified a private docker registry.
        cmd_login = ""
        if 'docker_login_config' in self.docker_config:
            # TODO(tian): Maybe support a command to get the login password?
            docker_login_config = DockerLoginConfig(
                **self.docker_config['docker_login_config'])
            cmd_login = f'{self.docker_cmd} login --username '\
                f'{docker_login_config.username} '\
                f'--password {docker_login_config.password} '\
                f'{docker_login_config.server};'
            # We automatically add the server prefix to the image name if
            # the user did not add it.
            server_prefix = f'{docker_login_config.server}/'
            if not specific_image.startswith(server_prefix):
                specific_image = f'{server_prefix}{specific_image}'

        if self.docker_config.get('pull_before_run', True):
            assert specific_image, ('Image must be included in config if ' +
                                    'pull_before_run is specified')
            cmd_pull = f'{self.docker_cmd} pull {specific_image};'
        else:
            cmd_pull = f'{self.docker_cmd} image inspect {specific_image} '\
                '1> /dev/null  2>&1 || '\
                f'{self.docker_cmd} pull {specific_image};'

        logger.info(f'Starting container {self.container_name} with image '
                    f'{specific_image}')

        cmd_run = ""
        container_running = 'Up' in status.get('container_status', False)
        if container_running:
            running_image = status.get('container_image', None)
            if running_image != specific_image:
                logger.error(
                    f'A container with name {self.container_name} is running '
                    f'image {running_image} instead of {specific_image} (which '
                    'was provided in the YAML)')
        else:
            # Edit docker config first to avoid disconnecting the container
            # from GPUs when a systemctl command is called. This is a known
            # issue with nvidia container toolkit:
            # https://github.com/NVIDIA/nvidia-container-toolkit/issues/48
            cmd_run ='[ -f /etc/docker/daemon.json ] || '\
                'echo "{}" | sudo tee /etc/docker/daemon.json;'\
                'sudo jq \'.["exec-opts"] = ["native.cgroupdriver=cgroupfs"]\' '\
                '/etc/docker/daemon.json > /tmp/daemon.json;'\
                'sudo mv /tmp/daemon.json /etc/docker/daemon.json;'\
                'sudo systemctl restart docker;'
            user_docker_run_options = self.docker_config.get('run_options', [])
            start_command = docker_start_cmds(
                specific_image,
                self.container_name,
                self._configure_runtime(self._auto_configure_shm(
                    user_docker_run_options,
                    available_memory=status.get("mem_available_in_kb", None)),
                                        runtime_output=status.get(
                                            "docker_runtime", None)),
                self.docker_cmd,
            )
            cmd_run += "\n" + start_command + ";"

        # Copy local authorized_keys to docker container.
        # Stop and disable jupyter service. This is to avoid port conflict on
        # 8080 if we use default deep learning image in GCP, and 8888 if we use
        # default deep learning image in Azure.
        # Azure also has a jupyterhub service running on 8081, so we stop and
        # disable that too.
        container_name = constants.DEFAULT_DOCKER_CONTAINER_NAME
        cmd_copy = f'{self.docker_cmd} cp ~/.ssh/authorized_keys '\
            f'{container_name}:/tmp/host_ssh_authorized_keys;'\
            'sudo systemctl stop jupyter > /dev/null 2>&1 || true;'\
            'sudo systemctl disable jupyter > /dev/null 2>&1 || true;'\
            'sudo systemctl stop jupyterhub > /dev/null 2>&1 || true;'\
            'sudo systemctl disable jupyterhub > /dev/null 2>&1 || true;'

        cmd_action = f"{cmd_login} {cmd_pull} {cmd_run} {cmd_copy}"
        cmd_result = self._run(cmd_action)
        # logger.debug(f"Command (host) result: {cmd_result}")

        # SkyPilot: Setup Commands.
        # TODO(zhwu): the following setups should be aligned with the kubernetes
        # pod setup, like provision.kubernetes.instance::_set_env_vars_in_pods
        # TODO(tian): These setup commands assumed that the container is
        # debian-based. We should make it more general.
        # Most of docker images are using root as default user, so we set an
        # alias for sudo to empty string, therefore any sudo in the following
        # commands won't fail.
        # Disable apt-get from asking user input during installation.
        # see https://askubuntu.com/questions/909277/avoiding-user-interaction-with-tzdata-when-installing-certbot-in-a-docker-contai  # pylint: disable=line-too-long
        cmd_d_shell = f'echo \'{command_runner.ALIAS_SUDO_TO_EMPTY_FOR_ROOT_CMD}\' '\
            '>> ~/.bashrc;'\
            'echo "export DEBIAN_FRONTEND=noninteractive" >> ~/.bashrc;'\
            'source ~/.bashrc;'
        # Install dependencies.
        cmd_d_dep = f'sudo apt-get update; '\
            'sudo apt-get -o DPkg::Options::="--force-confnew" install -y '\
            'rsync curl wget patch openssh-server python3-pip fuse;'
        # Our mount script will install gcsfuse without fuse package.
        # We need to install fuse package first to enable storage mount.
        # The dpkg option is to suppress the prompt for fuse installation.

        # Change the default port of sshd from 22 to DEFAULT_DOCKER_PORT.
        # Append the host VM's authorized_keys to the container's authorized_keys.
        # This allows any machine that can ssh into the host VM to ssh into the
        # container.
        # Last command here is to eliminate the error
        # `mesg: ttyname failed: inappropriate ioctl for device`.
        # see https://www.educative.io/answers/error-mesg-ttyname-failed-inappropriate-ioctl-for-device  # pylint: disable=line-too-long
        port = constants.DEFAULT_DOCKER_PORT
        # pylint: disable=anomalous-backslash-in-string
        cmd_d_port = f'sudo sed -i "s/#Port 22/Port {port}/" /etc/ssh/sshd_config;'\
            'mkdir -p ~/.ssh;'\
            'cat /tmp/host_ssh_authorized_keys >> ~/.ssh/authorized_keys;'\
            'sudo service ssh start;'\
            'sudo sed -i "s/mesg n/tty -s \&\& mesg n/" ~/.profile;'\
            f'{SETUP_ENV_VARS_CMD};'

        cmd_d_action = f"{cmd_d_shell} {cmd_d_dep} {cmd_d_port}"
        cmd_d_result = self._run(cmd_d_action, run_env='docker')
        # logger.debug(f"Command (docker) result: {cmd_d_result}")

        # SkyPilot: End of Setup Commands.
        docker_user = self._run('whoami', run_env='docker')
        self.initialized = True
        return docker_user

    def _docker_expand_user(self, string, any_char=False):
        user_pos = string.find('~')
        if user_pos > -1:
            if self.home_dir is None:
                cmd = (f'{self.docker_cmd} exec {self.container_name} '
                       'printenv HOME')
                self.home_dir = self._run(cmd, separate_stderr=True)
                # Check for unexpected newline in home directory, which can be
                # a common issue when the output is mixed with stderr.
                assert '\n' not in self.home_dir, (
                    'Unexpected newline in home directory '
                    f'({{self.home_dir}}) retrieved with {cmd}')

            if any_char:
                return string.replace('~/', self.home_dir + '/')

            elif not any_char and user_pos == 0:
                return string.replace('~', self.home_dir, 1)

        return string

    def _configure_runtime(self,
                           run_options: List[str],
                           runtime_output=None) -> List[str]:
        if self.docker_config.get('disable_automatic_runtime_detection'):
            return run_options

        if runtime_output is None:
            runtime_output = (self._run(f'{self.docker_cmd} ' +
                                        'info -f "{{.Runtimes}}"'))
        if 'nvidia-container-runtime' in runtime_output:
            try:
                self._run('nvidia-smi', log_err_when_fail=False)
                return run_options + ['--runtime=nvidia']
            except Exception as e:  # pylint: disable=broad-except
                logger.debug(
                    'Nvidia Container Runtime is present in the docker image'
                    'specified, but no GPUs found on the cluster. It should '
                    'still if the cluster is expected to have no GPU.\n'
                    f'  Details for nvidia-smi: {e}')
                return run_options

        return run_options

    def _auto_configure_shm(self,
                            run_options: List[str],
                            available_memory=None) -> List[str]:
        if self.docker_config.get('disable_shm_size_detection'):
            return run_options
        for run_opt in run_options:
            if '--shm-size' in run_opt:
                logger.info('Bypassing automatic SHM-Detection because of '
                            f'`run_option`: {run_opt}')
                return run_options
        try:
            if available_memory is None:
                shm_output = self._run('cat /proc/meminfo || true')
                available_memory = int([
                    ln for ln in shm_output.split('\n') if 'MemAvailable' in ln
                ][0].split()[1])
            available_memory_bytes = available_memory * 1024
            # Overestimate SHM size by 10%
            shm_size = min(
                (available_memory_bytes *
                 DEFAULT_OBJECT_STORE_MEMORY_PROPORTION * 1.1),
                DEFAULT_OBJECT_STORE_MAX_MEMORY_BYTES,
            )
            return run_options + [f'--shm-size="{shm_size}b"']
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(
                f'Received error while trying to auto-compute SHM size {e}')
            return run_options
