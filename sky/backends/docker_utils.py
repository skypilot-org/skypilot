"""Utilities for docker image generation."""
import json
import os
import shutil
import subprocess
import tempfile
import textwrap
from typing import Dict, Optional, Tuple

import colorama

from sky.adaptors import docker
from sky import sky_logging
from sky import task as task_mod

from ray.autoscaler._private.cli_logger import cli_logger
from ray.autoscaler._private.command_runner import DockerCommandRunner
from ray.autoscaler._private.docker import check_docker_running_cmd

logger = sky_logging.init_logger(__name__)

# Add docker-cli from official docker image to support docker-in-docker.
# We copy instead of installing docker-cli to keep the image builds fast.
DOCKERFILE_TEMPLATE = r"""
FROM {base_image}
SHELL ["/bin/bash", "-c"]
COPY --from=docker:dind /usr/local/bin/docker /usr/local/bin/
RUN apt-get update && apt-get -y install sudo
""".strip()

DOCKERFILE_SETUPCMD = """RUN {setup_command}"""
DOCKERFILE_COPYCMD = """COPY {copy_command}"""
DOCKERFILE_RUNCMD = """CMD {run_command}"""

# Docker default options
DEFAULT_DOCKER_CONTAINER_NAME = 'sky_container'
DEFAULT_DOCKER_PORT = '10022'

CONDA_SETUP_PREFIX = '. $(conda info --base)/etc/profile.d/conda.sh 2> ' \
                     '/dev/null || true '

SKY_DOCKER_SETUP_SCRIPT = 'sky_setup.sh'
SKY_DOCKER_RUN_SCRIPT = 'sky_run.sh'
SKY_DOCKER_WORKDIR = 'sky_workdir'


def create_dockerfile(
    base_image: str,
    setup_command: Optional[str],
    copy_path: str,
    build_dir: str,
    run_command: Optional[str] = None,
) -> Tuple[str, Dict[str, str]]:
    """Writes a valid dockerfile to the specified path.

    performs three operations:
    1. load base_image
    2. run some setup commands
    3. copy a directory to the image.
    the run/entrypoint can be optionally specified in the dockerfile or at
    execution time.

    Args:
        base_image: base image to inherit from
        setup_command: commands to run for setup. eg. "pip install numpy && apt
            install htop"
        copy_path: local path to copy into the image. these are placed in the
            root of the container.
        build_dir: Where to write the dockerfile and setup scripts
        run_command: cmd argument to the dockerfile. optional - can also be
            specified at runtime.

    Returns:
        Tuple[dockerfile_contents(str), img_metadata(Dict[str, str])]
        dockerfile_contents: contents of the dockerfile
        img_metadata: metadata related to the image, propagated to the backend
    """
    img_metadata = {}
    dockerfile_contents = DOCKERFILE_TEMPLATE.format(base_image=base_image)

    # Copy workdir to image
    workdir_name = ''
    if copy_path:
        workdir_name = os.path.basename(os.path.dirname(copy_path))
        # NOTE: This relies on copy_path being copied to build context.
        copy_docker_cmd = f'{workdir_name} /{workdir_name}/'
        dockerfile_contents += '\n' + DOCKERFILE_COPYCMD.format(
            copy_command=copy_docker_cmd)

    def add_script_to_dockerfile(dockerfile_contents: str,
                                 multiline_cmds: Optional[str],
                                 out_filename: str):
        # Converts multiline commands to a script and adds the script to the
        # dockerfile. You still need to add the docker command to run the
        # script (either as CMD or RUN).
        script_path = os.path.join(build_dir, out_filename)
        bash_codegen(workdir_name, multiline_cmds, script_path)

        # Add CMD to run setup
        copy_cmd = f'{out_filename} /sky/{out_filename}'

        # Set permissions and add to dockerfile
        dockerfile_contents += '\n' + DOCKERFILE_COPYCMD.format(
            copy_command=copy_cmd)
        dockerfile_contents += '\n' + DOCKERFILE_SETUPCMD.format(
            setup_command=f'chmod +x ./sky/{out_filename}')
        return dockerfile_contents

    # ===== SETUP ======
    dockerfile_contents = add_script_to_dockerfile(dockerfile_contents,
                                                   setup_command,
                                                   SKY_DOCKER_SETUP_SCRIPT)
    cmd = f'./sky/{SKY_DOCKER_SETUP_SCRIPT}'
    dockerfile_contents += '\n' + DOCKERFILE_SETUPCMD.format(setup_command=cmd)

    # ===== RUN ======
    dockerfile_contents = add_script_to_dockerfile(dockerfile_contents,
                                                   run_command,
                                                   SKY_DOCKER_RUN_SCRIPT)
    cmd = f'./sky/{SKY_DOCKER_RUN_SCRIPT}'
    dockerfile_contents += '\n' + DOCKERFILE_RUNCMD.format(run_command=cmd)

    # Write Dockerfile
    with open(os.path.join(build_dir, 'Dockerfile'), 'w') as f:
        f.write(dockerfile_contents)

    img_metadata['workdir_name'] = workdir_name
    return dockerfile_contents, img_metadata


def _execute_build(tag, context_path):
    """
    Executes a dockerfile build with the given context.
    The context path must contain the dockerfile and all dependencies.
    """
    assert tag is not None, 'Image tag cannot be None - have you specified a ' \
                            'task name? '
    docker_client = docker.from_env()
    try:
        unused_image, unused_build_logs = docker_client.images.build(
            path=context_path, tag=tag, rm=True, quiet=False)
    except docker.build_error() as e:
        style = colorama.Style
        fore = colorama.Fore
        logger.error(f'{fore.RED}Image build for {tag} failed - are your setup '
                     f'commands correct? Logs below{style.RESET_ALL}')
        logger.error(
            f'{style.BRIGHT}Image context is available at {context_path}'
            f'{style.RESET_ALL}')
        for line in e.build_log:
            if 'stream' in line:
                logger.error(line['stream'].strip())
        raise


def get_docker_user(ip: str, cluster_config_file: str) -> str:
    """Find docker container username."""
    # pylint: disable=import-outside-toplevel
    from sky.backends import backend_utils
    from sky.utils import command_runner
    ssh_credentials = backend_utils.ssh_credential_from_yaml(
        cluster_config_file)
    runner = command_runner.SSHCommandRunner(ip, **ssh_credentials)
    whoami_returncode, whoami_stdout, whoami_stderr = runner.run(
        f'sudo docker exec {DEFAULT_DOCKER_CONTAINER_NAME} whoami',
        stream_logs=False,
        require_outputs=True)
    assert whoami_returncode == 0, (
        f'Failed to get docker container user. Return '
        f'code: {whoami_returncode}, Error: {whoami_stderr}')
    docker_user = whoami_stdout.strip()
    logger.debug(f'Docker container user: {docker_user}')
    return docker_user


def build_dockerimage(task: task_mod.Task,
                      tag: str) -> Tuple[str, Dict[str, str]]:
    """
    Builds a docker image for the given task.

    This method is responsible for:
    1. Create a temp directory to set the build context.
    2. Copy dockerfile to this directory and copy contents
    3. Run the dockerbuild
    """
    # Get tempdir
    temp_dir = tempfile.mkdtemp(prefix='sky_local_')

    # Create dockerfile
    if callable(task.run):
        raise ValueError(
            'Cannot build docker image for a task.run with function.')
    _, img_metadata = create_dockerfile(base_image=task.docker_image,
                                        setup_command=task.setup,
                                        copy_path=f'{SKY_DOCKER_WORKDIR}/',
                                        run_command=task.run,
                                        build_dir=temp_dir)

    dst = os.path.join(temp_dir, SKY_DOCKER_WORKDIR)
    if task.workdir is not None:
        # Copy workdir contents to tempdir
        shutil.copytree(os.path.expanduser(task.workdir), dst)
    else:
        # Create an empty dir
        os.makedirs(dst)

    logger.info(f'Using tempdir {temp_dir} for docker build.')

    # Run docker image build
    _execute_build(tag, context_path=temp_dir)

    # Clean up temp dir
    subprocess.run(['rm', '-rf', temp_dir], check=False)

    return tag, img_metadata


def build_dockerimage_from_task(
        task: task_mod.Task) -> Tuple[str, Dict[str, str]]:
    """ Builds a docker image from a Task"""
    assert task.name is not None, task
    tag, img_metadata = build_dockerimage(task, tag=task.name)
    return tag, img_metadata


def push_dockerimage(local_tag, remote_name):
    raise NotImplementedError('Pushing images is not yet implemented.')


def make_bash_from_multiline(codegen: str) -> str:
    """
    Makes a bash script from a multi-line string of commands.
    Automatically includes conda setup prefixes.
    Args:
        codegen: str: multiline commands to be converted to a shell script

    Returns:
        script: str: str of shell script that can be written to a file
    """
    script = [
        textwrap.dedent(f"""\
        #!/bin/bash
        set -e
        {CONDA_SETUP_PREFIX}"""),
        codegen,
    ]
    script = '\n'.join(script)
    return script


def bash_codegen(workdir_name: str,
                 multiline_cmds: Optional[str],
                 out_path: Optional[str] = None):
    # Generate commands (if they exist) script and write to file
    if not multiline_cmds:
        multiline_cmds = ''
    multiline_cmds = f'cd /{workdir_name}\n{multiline_cmds}'
    script_contents = make_bash_from_multiline(multiline_cmds)
    if out_path:
        with open(out_path, 'w') as fp:
            fp.write(script_contents)
    return script_contents


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
    """Generating docker start command without --rm."""
    del user  # unused

    # pylint: disable=import-outside-toplevel
    from ray.autoscaler.sdk import get_docker_host_mount_location

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
        '--name {}'.format(container_name),
        '-d',
        '-it',
        mount_flags,
        env_flags,
        user_options_str,
        '--net=host',
        image,
        'bash',
    ]
    return ' '.join(docker_run)


class SkyDockerCommandRunner(DockerCommandRunner):
    """A DockerCommandRunner that
        1. Run some custom setup commands;
        2. Reimplement docker stop to save the container after the host VM
           is shut down.

    The code is borrowed from
    `ray.autoscaler._private.command_runner.DockerCommandRunner`."""

    def _check_container_exited(self) -> bool:
        if self.initialized:
            return True
        output = (self.ssh_command_runner.run(
            check_docker_running_cmd(self.container_name, self.docker_cmd),
            with_output=True,
        ).decode('utf-8').strip())
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

        if self._check_container_exited():
            self.initialized = True
            self.run(f'docker start {self.container_name}', run_env='host')
            return True

        if self.docker_config.get('pull_before_run', True):
            assert specific_image, ('Image must be included in config if ' +
                                    'pull_before_run is specified')
            self.run('{} pull {}'.format(self.docker_cmd, specific_image),
                     run_env='host')
        else:

            self.run(f'{self.docker_cmd} image inspect {specific_image} '
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
                # Manualy rm here since --rm is not specified
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

        # Setup Commands.
        # Most of docker images are using root as default user, so we set an alias
        # for sudo to empty string, so any sudo in the following commands won't fail.
        # Disable apt-get from asking user input during installation.
        # see https://askubuntu.com/questions/909277/avoiding-user-interaction-with-tzdata-when-installing-certbot-in-a-docker-contai  #pylint: disable=line-too-long
        self.run('echo \'[ "$(whoami)" == "root" ] && alias sudo=""\' >> ~/.bashrc;'
                 'echo "export DEBIAN_FRONTEND=noninteractive" >> ~/.bashrc;')
        # Install dependencies.
        self.run('sudo apt-get update; sudo apt-get install -y rsync curl wget patch openssh-server;')
        # Copy local authorized_keys to docker container.
        container_name = DEFAULT_DOCKER_CONTAINER_NAME
        self.run(
            'rsync -e "docker exec -i" -avz ~/.ssh/authorized_keys '
            f'{container_name}:/tmp/host_ssh_authorized_keys',
            run_env='host')
        # Change the default port of sshd from 22 to DEFAULT_DOCKER_PORT.
        # Append the host VM's authorized_keys to the container's authorized_keys.
        # This allows any machine that can ssh into the host VM to ssh into the
        # container.
        # Last command here is to eliminate the error
        # `mesg: ttyname failed: inappropriate ioctl for device`.
        # see https://www.educative.io/answers/error-mesg-ttyname-failed-inappropriate-ioctl-for-device  #pylint: disable=line-too-long
        self.run(
            f'sudo sed -i "s/#Port 22/Port {DEFAULT_DOCKER_PORT}/" /etc/ssh/sshd_config;'
            'mkdir -p ~/.ssh;'
            'cat /tmp/host_ssh_authorized_keys >> ~/.ssh/authorized_keys;'
            'sudo service ssh start;'
            'sudo sed -i "s/mesg n/tty -s \&\& mesg n/" ~/.profile;')

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
