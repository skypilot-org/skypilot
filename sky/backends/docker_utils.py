"""Utilities for docker image generation."""
import os
import shutil
import subprocess
import tempfile
import textwrap
from typing import Dict, Optional, Tuple

import colorama

from sky import sky_logging
from sky import task as task_mod
from sky.adaptors import docker

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
    with open(os.path.join(build_dir, 'Dockerfile'), 'w',
              encoding='utf-8') as f:
        f.write(dockerfile_contents)

    img_metadata['workdir_name'] = workdir_name
    return dockerfile_contents, img_metadata


def _execute_build(tag, context_path):
    """Executes a dockerfile build with the given context.

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


def build_dockerimage(task: task_mod.Task,
                      tag: str) -> Tuple[str, Dict[str, str]]:
    """Builds a docker image for the given task.

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
    """Makes a bash script from a multi-line string of commands.

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
        with open(out_path, 'w', encoding='utf-8') as fp:
            fp.write(script_contents)
    return script_contents
