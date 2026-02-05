"""Base class for all logging agents."""
import abc
import os
import shlex
from typing import Any, Dict

from sky.skylet import constants
from sky.utils import resources_utils
from sky.utils import yaml_utils


class LoggingAgent(abc.ABC):
    """Base class for all logging agents.

    Each agent should implement the `get_setup_command` and
    `get_credential_file_mounts` methods to return the setup command and
    credential file mounts for the agent for provisioner to setup the agent
    on each node.
    """

    @abc.abstractmethod
    def get_setup_command(self,
                          cluster_name: resources_utils.ClusterName) -> str:
        pass

    @abc.abstractmethod
    def get_credential_file_mounts(self) -> Dict[str, str]:
        pass


class FluentbitAgent(LoggingAgent):
    """Base class for logging store that use fluentbit as the agent."""

    def get_setup_command(self,
                          cluster_name: resources_utils.ClusterName) -> str:
        install_cmd = (
            # pylint: disable=line-too-long
            'if ! command -v fluent-bit >/dev/null 2>&1 && [ ! -f /opt/fluent-bit/bin/fluent-bit ]; then '
            'sudo apt-get update; sudo apt-get install -y gnupg; '
            # pylint: disable=line-too-long
            'sudo sh -c \'curl -L https://packages.fluentbit.io/fluentbit.key | gpg --dearmor > /usr/share/keyrings/fluentbit-keyring.gpg\'; '
            # pylint: disable=line-too-long
            'os_id=$(grep -oP \'(?<=^ID=).*\' /etc/os-release 2>/dev/null || lsb_release -is 2>/dev/null | tr \'[:upper:]\' \'[:lower:]\'); '
            # pylint: disable=line-too-long
            'codename=$(grep -oP \'(?<=VERSION_CODENAME=).*\' /etc/os-release 2>/dev/null || lsb_release -cs 2>/dev/null); '
            # pylint: disable=line-too-long
            'echo "deb [signed-by=/usr/share/keyrings/fluentbit-keyring.gpg] https://packages.fluentbit.io/$os_id/$codename $codename main" | sudo tee /etc/apt/sources.list.d/fluent-bit.list; '
            'sudo apt-get update; '
            'sudo apt-get install -y fluent-bit; '
            'fi')
        cfg = self.fluentbit_config(cluster_name)
        cfg_path = os.path.join(constants.LOGGING_CONFIG_DIR, 'fluentbit.yaml')
        config_cmd = (f'mkdir -p {constants.LOGGING_CONFIG_DIR} && '
                      f'echo {shlex.quote(cfg)} > {cfg_path}')
        kill_prior_cmd = (
            'if [ -f "/tmp/fluentbit.pid" ]; then '
            # pylint: disable=line-too-long
            'echo "Killing prior fluent-bit process $(cat /tmp/fluentbit.pid)"; '
            'kill "$(cat /tmp/fluentbit.pid)" || true; '
            'fi')
        start_cmd = ('nohup $(command -v fluent-bit || '
                     'echo "/opt/fluent-bit/bin/fluent-bit") '
                     f'-c {cfg_path} > /tmp/fluentbit.log 2>&1 & '
                     'echo $! > /tmp/fluentbit.pid')
        return ('set -e; '
                f'{install_cmd}; '
                f'{config_cmd}; '
                f'{kill_prior_cmd}; '
                f'{start_cmd}')

    def fluentbit_config(self,
                         cluster_name: resources_utils.ClusterName) -> str:
        cfg_dict = {
            'parsers': [{
                'name': 'sky-ray-parser',
                'format': 'regex',
                # pylint: disable=line-too-long
                'regex': r'(?:\x1b\[[\d;]+m)?\((?<worker_name>[^,]+)(?:,\s*rank=(?<rank>\d+))?(?:,\s*pid=(?<pid>\d+))(?:,\s*ip=(?<ip>[\d.]+))?\)(?:\x1b\[[\d;]+m)?\s*(?<log_line>.*)',
                'types': 'rank:integer pid:integer',
            }],
            'pipeline': {
                'inputs': [{
                    'name': 'tail',
                    'path': f'{constants.SKY_LOGS_DIRECTORY}/*/*.log',
                    'path_key': 'log_path',
                    # Shorten the refresh interval from 60s to 1s since every
                    # job creates a new log file and we must be responsive
                    # for this: the VM might be autodown within a minute
                    # right after the job completion.
                    'refresh_interval': 1,
                }],
                'filters': [{
                    'name': 'parser',
                    'match': '*',
                    'key_name': 'log',
                    'parser': 'sky-ray-parser',
                    'preserve_key': 'on',  # preserve field for backwards compat
                    'reserve_data': 'on',
                }],
                'outputs': [self.fluentbit_output_config(cluster_name)],
            }
        }
        return yaml_utils.dump_yaml_str(cfg_dict)

    @abc.abstractmethod
    def fluentbit_output_config(
            self, cluster_name: resources_utils.ClusterName) -> Dict[str, Any]:
        pass
