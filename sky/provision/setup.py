from typing import List
from sky.utils import command_runner, subprocess_utils


def start_ray(ssh_runners: List[command_runner.SSHCommandRunner],
              head_private_ip: str):
    ray_prlimit = (
        'which prlimit && for id in $(pgrep -f raylet/raylet); '
        'do sudo prlimit --nofile=1048576:1048576 --pid=$id || true; done;')

    ssh_runners[0].run('ray stop; ray start --disable-usage-stats --head '
                       '--port=6379 --object-manager-port=8076;' + ray_prlimit,
                       stream_logs=False)

    def _setup_ray_worker(runner: command_runner.SSHCommandRunner):
        # for cmd in config_from_yaml['worker_start_ray_commands']:
        #     cmd = cmd.replace('$RAY_HEAD_IP', ip_list[0][0])
        #     runner.run(cmd)
        runner.run(f'ray stop; ray start --disable-usage-stats '
                   f'--address={head_private_ip}:6379;' + ray_prlimit,
                   stream_logs=False)

    subprocess_utils.run_in_parallel(_setup_ray_worker, ssh_runners[1:])
