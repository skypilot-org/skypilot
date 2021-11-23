import sky
from sky.backends import docker_utils
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if __name__ == '__main__':
    setup_command = 'apt-get update && apt-get install htop && pip install '  # pip install ...
    base_image = 'ubuntu:20.04'
    run_cmd = "echo Hi && sleep 5 && echo Bye"
    # The final dir is put in root
    work_dir = '/mnt/d/Romil/Berkeley/Research/sky-experiments/prototype/sky/'  # /sky/

    with sky.Dag():
        t = sky.Task(name="mytask",
                     workdir=work_dir,
                     setup=setup_command,
                     docker_image=base_image,
                     run=run_cmd)

    logger.info(f"Starting docker build for {t.name}.")
    tag = docker_utils.build_dockerimage_from_task(t)
    logger.info(f"Build successful. Tag: {tag}.")
    logger.info(f"Try running docker run -it --rm {tag}")
    logger.info(
        f"Debug your container with docker run -it --rm {tag} /bin/bash")

    # TODO(romilb): Need to implement cloud-agnostic push pipeline - ECR/GCR/ACR
    logger.info(f"Trying to push image {tag}")
    remote_tag = docker_utils.push_dockerimage(tag, t.name)
    logger.info(
        f"Push successful. From any machine, try docker run -it --rm {remote_tag}"
    )
