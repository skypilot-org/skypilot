import subprocess
from typing import Dict, List, Optional, Union

IPAddr = str


def post_setup_fn(ip_list: List[IPAddr]) -> Dict[IPAddr, str]:
    #  ssh-keygen -f ~/.ssh/id_rsa -P ""
    # cat ~/.ssh/id_rsa.pub | ssh -i ray_bootstrap_key.pem ubuntu@{server_ip_} \"mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys\"
    command_dict = {}
    head_run_str = "ssh-keygen -f ~/.ssh/id_rsa -P \"\" <<< y"
    if len(ip_list) > 1:
        for i, ip in enumerate(ip_list[1:]):
            append_str = f" && cat ~/.ssh/id_rsa.pub | ssh -i ~/ray_bootstrap_key.pem -o StrictHostKeyChecking=no ubuntu@{ip} \"mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys\""
            head_run_str = head_run_str + append_str
    return {ip_list[0]: head_run_str}


def run_command_on_ip_via_ssh(ip: str,
                              command: str,
                              private_key: str,
                              container_name: Optional[str],
                              user: str = 'ubuntu') -> None:
    if container_name is not None:
        command = command.replace('\\', '\\\\').replace('"', '\\"')
        command = f'docker exec {container_name} /bin/bash -c "{command}"'
    cmd = [
        'ssh',
        '-i',
        private_key,
        '-o',
        'StrictHostKeyChecking=no',
        '{}@{}'.format(user, ip),
        command  # TODO: shlex.quote() doesn't work.  Is it needed in a list?
    ]
    print(cmd)
    proc = subprocess.Popen(cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            universal_newlines=True)
    outs, errs = proc.communicate()
    print(outs, errs)


run_commands = post_setup_fn(['3.141.11.215', '18.191.109.219'])
print(run_commands)
for k, v in run_commands.items():
    run_command_on_ip_via_ssh(k, v, "~/.ssh/sky-key", None)
