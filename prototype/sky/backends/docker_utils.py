"""Utilities for docker image generation."""
import os
import shutil
import subprocess
import tempfile

import docker
from sky import logging, Task

logger = logging.init_logger(__name__)

DOCKERFILE_TEMPLATE = """
FROM {base_image}
RUN {setup_command}
COPY {copy_command}
""".strip()

DOCKERFILE_RUNCMD = """CMD {run_command}"""


def create_dockerfile(base_image: str,
                      setup_command: str,
                      copy_path: str,
                      output_path: str = None,
                      run_command: str = None,
                      ) -> str:
    """
    Writes a valid dockerfile to the specified path. Supports only three operations:
    1. Load base_image
    2. Run some setup commands
    3. Copy a directory to the image.
    The run/entrypoint can be optionally specified in the dockerfile or at execution time.

    :param base_image: base image to inherit from
    :param setup_command: commands to run for setup. Eg. "pip install numpy && apt install htop"
    :param copy_paths: Local path to copy into the image. These are placed in the root of the container.
    :param output_path: if specified - where to write the dockerfile. Else dockerfile is not written to disk.
    :param run_command: CMD argument to the dockerfile. Optional - can also be specified at runtime.
    :return: contents of the dockerfile
    """
    dir_name = os.path.basename(os.path.dirname(copy_path))
    copy_command = f"{dir_name} /{dir_name}/" # NOTE: This relies on copy_path being copied to build context.
    dockerfile_contents = DOCKERFILE_TEMPLATE.format(base_image=base_image,
                                                     setup_command=setup_command,
                                                     copy_command=copy_command)

    if run_command:
        dockerfile_contents += "\n" + DOCKERFILE_RUNCMD.format(run_command=run_command)

    if output_path:
        with open(output_path, 'w') as f:
            f.write(dockerfile_contents)
    return dockerfile_contents

def _execute_build(tag, context_path):
    """
    Executes a dockerfile build with the given context.
    The context path must contain the dockerfile and all dependencies.
    """
    docker_client = docker.from_env()
    docker_client.images.build(path=context_path,
                               tag=tag,
                               rm=True,
                               quiet=False)

def build_dockerimage(dockerfile_contents, copy_path, tag):
    """
    This method is responsible for:
    1. Create a temp directory to set the build context.
    2. Copy dockerfile to this directory and copy contents
    3. Run the dockerbuild
    """
    # Get tempdir
    # TODO(romilb): Use contextmanager here instead of manual clean up when done with debugging
    temp_dir = tempfile.mkdtemp(prefix='sky')

    # Write dockerfile to tempdir.
    dockerfile_path = os.path.join(temp_dir, 'Dockerfile')
    with open(dockerfile_path, 'w') as f:
        f.write(dockerfile_contents)

    # Copy copy_path contents to tempdir TODO(romilb): See if you can use symlinks.
    copy_dir_name = os.path.basename(os.path.dirname(copy_path))
    dst = os.path.join(temp_dir, copy_dir_name)
    shutil.copytree(copy_path, dst)
    logger.info(f"Using tempdir {temp_dir} for docker build.")

    # Run docker image build
    _execute_build(tag, context_path=temp_dir)

    # Clean up temp dir
    subprocess.run(["rm", "-rf", temp_dir])

    return tag


def build_dockerimage_from_task(task: Task):
    """ Builds a docker image from a tag"""
    dockerfile_contents = create_dockerfile(base_image=task.docker_image,
                                            setup_command=task.setup,
                                            copy_path=task.workdir,
                                            run_command=task.run)
    tag = build_dockerimage(dockerfile_contents, task.workdir, tag=task.name)
    return tag