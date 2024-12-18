"""Sky's DockerCommandRunner."""
import json
import os
import time
from typing import Dict

import click
from ray.autoscaler._private.cli_logger import cli_logger
from ray.autoscaler._private.command_runner import DockerCommandRunner
from ray.autoscaler._private.docker import check_docker_running_cmd
from ray.autoscaler.sdk import get_docker_host_mount_location

from sky.provision import docker_utils
from sky.skylet import constants


def docker_start_cmds(
    user,
    image,
    mount_dict,
    container_name,
    user_options,
    cluster_name,
    home_directory,
    docker_cmd,
):
    """Generating docker start command without --rm.
    
    The code is borrowed from `ray.autoscaler._private.docker`.

    Changes we made:
        1. Remove --rm flag to keep the container after `ray stop` is executed;
        2. Add options to enable fuse.
    """
    del user  # unused

    docker_mount_prefix = get_docker_host_mount_location(cluster_name)
    mount = {f'{docker_mount_prefix}/{dst}': dst for dst in mount_dict}

    mount_flags = ' '.join([
        '-v {src}:{dest}'.format(src=k,
                                 dest=v.replace('~/', home_directory + '/'))
        for k, v in mount.items()
    ])

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
        mount_flags,
        env_flags,
        user_options_str,
        '--net=host',
        # SkyPilot: Add following options to enable fuse.
        '--cap-add=SYS_ADMIN',
        '--device=/dev/fuse',
        '--security-opt=apparmor:unconfined',
        '--entrypoint=/bin/bash',
        image,
    ]
    return ' '.join(docker_run)


class SkyDockerCommandRunner(DockerCommandRunner):
    """A DockerCommandRunner that
        1. Run some custom setup commands;
        2. Reimplement docker stop to save the container after the host VM
           is shut down;
        3. Allow docker login before running the container, to enable pulling
           from private docker registry.

    The code is borrowed from
    `ray.autoscaler._private.command_runner.DockerCommandRunner`.
    """

    def _run_with_retry(self, cmd, **kwargs):
        """Run a command with retries for docker."""
        cnt = 0
        max_retry = 3
        while True:
            try:
                return self.run(cmd, **kwargs)
            except click.ClickException as e:
                # We retry the command if it fails, because docker commands can
                # fail due to the docker daemon not being ready yet.
                # Ray command runner raise ClickException when the command
                # fails.
                cnt += 1
                if cnt >= max_retry:
                    raise e
                cli_logger.warning(
                    f'Failed to run command {cmd!r}. '
                    f'Retrying in 10 seconds. Retry count: {cnt}')
                time.sleep(10)

    # SkyPilot: New function to check whether a container is exited
    # (but not removed). This is due to previous `sky stop` command,
    # which will stop the container but not remove it.
    def _check_container_exited(self) -> bool:
        if self.initialized:
            return True
        cnt = 0
        max_retry = 3
        cmd = check_docker_running_cmd(self.container_name, self.docker_cmd)
        # We manually retry the command based on the output, as the command will
        # not fail even if the docker daemon is not ready, due to the underlying
        # usage of `|| true` in the command.
        while True:
            output = (self.run(cmd, with_output=True,
                               run_env='host').decode('utf-8').strip())
            if docker_utils.DOCKER_PERMISSION_DENIED_STR in output:
                cnt += 1
                if cnt >= max_retry:
                    raise click.ClickException(
                        f'Failed to run command {cmd!r}. '
                        f'Retry count: {cnt}. Output: {output}')
                cli_logger.warning(
                    f'Failed to run command {cmd!r}. '
                    f'Retrying in 10 seconds. Retry count: {cnt}')
                time.sleep(10)
            else:
                break
        return 'false' in output.lower(
        ) and 'no such object' not in output.lower()

    def run_init(self, *, as_head: bool, file_mounts: Dict[str, str],
                 sync_run_yet: bool):
        bootstrap_mounts = [
            '~/ray_bootstrap_config.yaml', '~/ray_bootstrap_key.pem'
        ]

        specific_image = self.docker_config.get(
            f'{"head" if as_head else "worker"}_image',
            self.docker_config.get('image'))

        self._check_docker_installed()

        # SkyPilot: Check if the container is exited but not removed.
        # If true, then we can start the container directly.
        # Notice that we will skip all setup commands, so we need to
        # manually start the ssh service.
        # We also add retries when checking the container status to make sure
        # the docker daemon is ready, as it may not be ready immediately after
        # the VM is started.
        if self._check_container_exited():
            self.initialized = True
            self.run(f'docker start {self.container_name}', run_env='host')
            self.run('sudo service ssh start')
            return True

        # SkyPilot: Docker login if user specified a private docker registry.
        if "docker_login_config" in self.docker_config:
            # TODO(tian): Maybe support a command to get the login password?
            docker_login_config: docker_utils.DockerLoginConfig = self.docker_config[
                "docker_login_config"]
            self._run_with_retry(
                f'{self.docker_cmd} login --username '
                f'{docker_login_config.username} --password '
                f'{docker_login_config.password} {docker_login_config.server}')
            # We automatically add the server prefix to the image name if
            # the user did not add it.
            server_prefix = f'{docker_login_config.server}/'
            if not specific_image.startswith(server_prefix):
                specific_image = f'{server_prefix}{specific_image}'

        if self.docker_config.get('pull_before_run', True):
            assert specific_image, ('Image must be included in config if '
                                    'pull_before_run is specified')
            self._run_with_retry(f'{self.docker_cmd} pull {specific_image}',
                                 run_env='host')
        else:
            self._run_with_retry(
                f'{self.docker_cmd} image inspect {specific_image} '
                '1> /dev/null  2>&1 || '
                f'{self.docker_cmd} pull {specific_image}')

        # Bootstrap files cannot be bind mounted because docker opens the
        # underlying inode. When the file is switched, docker becomes outdated.
        cleaned_bind_mounts = file_mounts.copy()
        for mnt in bootstrap_mounts:
            cleaned_bind_mounts.pop(mnt, None)

        docker_run_executed = False

        container_running = self._check_container_status()
        requires_re_init = False
        if container_running:
            requires_re_init = self._check_if_container_restart_is_needed(
                specific_image, cleaned_bind_mounts)
            if requires_re_init:
                self.run(f'{self.docker_cmd} stop {self.container_name}',
                         run_env='host')
                # Manually rm here since --rm is not specified
                self.run(f'{self.docker_cmd} rm {self.container_name}',
                         run_env='host')

        if (not container_running) or requires_re_init:
            if not sync_run_yet:
                # Do not start the actual image as we need to run file_sync
                # first to ensure that all folders are created with the
                # correct ownership. Docker will create the folders with
                # `root` as the owner.
                return True
            # Get home directory
            image_env = (self.ssh_command_runner.run(
                f'{self.docker_cmd} ' + 'inspect -f "{{json .Config.Env}}" ' +
                specific_image,
                with_output=True,
            ).decode().strip())
            home_directory = '/root'
            try:
                for env_var in json.loads(image_env):
                    if env_var.startswith('HOME='):
                        home_directory = env_var.split('HOME=')[1]
                        break
            except json.JSONDecodeError as e:
                cli_logger.error(
                    'Unable to deserialize `image_env` to Python object. '
                    f'The `image_env` is:\n{image_env}')
                raise e

            # Edit docker config first to avoid disconnecting the container
            # from GPUs when a systemctl command is called. This is a known
            # issue with nvidia container toolkit:
            # https://github.com/NVIDIA/nvidia-container-toolkit/issues/48
            self.run(
                '[ -f /etc/docker/daemon.json ] || '
                'echo "{}" | sudo tee /etc/docker/daemon.json;'
                'sudo jq \'.["exec-opts"] = ["native.cgroupdriver=cgroupfs"]\' '
                '/etc/docker/daemon.json > /tmp/daemon.json;'
                'sudo mv /tmp/daemon.json /etc/docker/daemon.json;'
                'sudo systemctl restart docker',
                run_env='host')

            user_docker_run_options = self.docker_config.get(
                'run_options', []) + self.docker_config.get(
                    f'{"head" if as_head else "worker"}_run_options', [])
            start_command = docker_start_cmds(
                self.ssh_command_runner.ssh_user,
                specific_image,
                cleaned_bind_mounts,
                self.container_name,
                self._configure_runtime(
                    self._auto_configure_shm(user_docker_run_options)),
                self.ssh_command_runner.cluster_name,
                home_directory,
                self.docker_cmd,
            )
            self.run(start_command, run_env='host')
            docker_run_executed = True

        # SkyPilot: Setup Commands.
        # TODO(tian): These setup commands assumed that the container is
        # debian-based. We should make it more general.
        # Most of docker images are using root as default user, so we set an
        # alias for sudo to empty string, therefore any sudo in the following
        # commands won't fail.
        # Disable apt-get from asking user input during installation.
        # see https://askubuntu.com/questions/909277/avoiding-user-interaction-with-tzdata-when-installing-certbot-in-a-docker-contai  # pylint: disable=line-too-long
        self.run(
            'echo \'[ "$(whoami)" == "root" ] && alias sudo=""\' >> ~/.bashrc;'
            'echo "export DEBIAN_FRONTEND=noninteractive" >> ~/.bashrc;')
        # Install dependencies.
        self.run(
            'sudo apt-get update; '
            # Our mount script will install gcsfuse without fuse package.
            # We need to install fuse package first to enable storage mount.
            # The dpkg option is to suppress the prompt for fuse installation.
            'sudo apt-get -o DPkg::Options::="--force-confnew" install '
            '-y rsync curl wget patch openssh-server python3-pip fuse;')

        # Copy local authorized_keys to docker container.
        # Stop and disable jupyter service. This is to avoid port conflict on
        # 8080 if we use default deep learning image in GCP, and 8888 if we use
        # default deep learning image in Azure.
        # Azure also has a jupyterhub service running on 8081, so we stop and
        # disable that too.
        container_name = constants.DEFAULT_DOCKER_CONTAINER_NAME
        self.run(
            'rsync -e "docker exec -i" -avz ~/.ssh/authorized_keys '
            f'{container_name}:/tmp/host_ssh_authorized_keys;'
            'sudo systemctl stop jupyter > /dev/null 2>&1 || true;'
            'sudo systemctl disable jupyter > /dev/null 2>&1 || true;'
            'sudo systemctl stop jupyterhub > /dev/null 2>&1 || true;'
            'sudo systemctl disable jupyterhub > /dev/null 2>&1 || true;',
            run_env='host')

        # Change the default port of sshd from 22 to DEFAULT_DOCKER_PORT.
        # Append the host VM's authorized_keys to the container's authorized_keys.
        # This allows any machine that can ssh into the host VM to ssh into the
        # container.
        # Last command here is to eliminate the error
        # `mesg: ttyname failed: inappropriate ioctl for device`.
        # see https://www.educative.io/answers/error-mesg-ttyname-failed-inappropriate-ioctl-for-device  # pylint: disable=line-too-long
        port = constants.DEFAULT_DOCKER_PORT
        # pylint: disable=anomalous-backslash-in-string
        self.run(f'sudo sed -i "s/#Port 22/Port {port}/" /etc/ssh/sshd_config;'
                 'mkdir -p ~/.ssh;'
                 'cat /tmp/host_ssh_authorized_keys >> ~/.ssh/authorized_keys;'
                 'sudo service ssh start;'
                 'sudo sed -i "s/mesg n/tty -s \&\& mesg n/" ~/.profile;')

        # SkyPilot: End of Setup Commands.

        # Explicitly copy in ray bootstrap files.
        for mount in bootstrap_mounts:
            if mount in file_mounts:
                if not sync_run_yet:
                    # NOTE(ilr) This rsync is needed because when starting from
                    #  a stopped instance,  /tmp may be deleted and `run_init`
                    # is called before the first `file_sync` happens
                    self.run_rsync_up(file_mounts[mount], mount)
                self.ssh_command_runner.run(
                    'rsync -e "{cmd} exec -i" -avz {src} {container}:{dst}'.
                    format(
                        cmd=self.docker_cmd,
                        src=os.path.join(
                            self._get_docker_host_mount_location(
                                self.ssh_command_runner.cluster_name),
                            mount,
                        ),
                        container=self.container_name,
                        dst=self._docker_expand_user(mount),
                    ))
                try:
                    # Check if the current user has read permission.
                    # If they do not, try to change ownership!
                    self.run(f'cat {mount} >/dev/null 2>&1 || '
                             f'sudo chown $(id -u):$(id -g) {mount}')
                # pylint: disable=broad-except
                except Exception:
                    lsl_string = (self.run(
                        f'ls -l {mount}',
                        with_output=True).decode('utf-8').strip())
                    # The string is of format <Permission> <Links>
                    # <Owner> <Group> <Size> <Date> <Name>
                    permissions = lsl_string.split(' ')[0]
                    owner = lsl_string.split(' ')[2]
                    group = lsl_string.split(' ')[3]
                    current_user = (self.run(
                        'whoami', with_output=True).decode('utf-8').strip())
                    cli_logger.warning(
                        f'File ({mount}) is owned by user:{owner} and group:'
                        f'{group} with permissions ({permissions}). The '
                        f'current user ({current_user}) does not have '
                        'permission to read these files, and Ray may not be '
                        'able to autoscale. This can be resolved by '
                        'installing `sudo` in your container, or adding a '
                        f'command like "chown {current_user} {mount}" to '
                        'your `setup_commands`.')
        self.initialized = True
        return docker_run_executed
