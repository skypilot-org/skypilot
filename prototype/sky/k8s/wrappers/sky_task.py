from typing import List


class SkyRayTask(object):
    def __init__(self,
                 name: str,
                 run_command: List[str],
                 run_args: List[str],
                 docker_image: str = None,
                 setup_commands: List[str] = None,
                 files_to_copy: List[str] = None):
        """
        :param run_command: shell command to run for the job
        :param run_args: args to the run_command
        :param docker_image: Public docker image to use
        :param setup_commands: Setup commands to use for building the docker image if docker image is not specified
        :param files_to_copy: Files to copy to the docker image if docker image is not specified
        """
        self.name = name
        self.run_command = run_command
        self.run_args = run_args
        self.docker_image = docker_image
        self.setup_commands = setup_commands
        self.files_to_copy = files_to_copy

        if self.docker_image is None:
            if self.setup_commands is None or self.files_to_copy is None:
                raise ValueError("You should either specify the docker image or the setup commands")
            else:
                raise NotImplementedError("Building docker images is not supported yet.")