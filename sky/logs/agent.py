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
            'if ! command -v fluent-bit >/dev/null 2>&1; then '
            'sudo apt-get install -y gnupg; '
            # pylint: disable=line-too-long
            'curl https://raw.githubusercontent.com/fluent/fluent-bit/master/install.sh | sh; '
            'fi')
        cfg = self.fluentbit_config(cluster_name)
        cfg_path = os.path.join(constants.LOGGING_CONFIG_DIR, 'fluentbit.yaml')
        config_cmd = (f'mkdir -p {constants.LOGGING_CONFIG_DIR} && '
                      f'echo {shlex.quote(cfg)} > {cfg_path}')
        start_cmd = ('nohup $(command -v fluent-bit || '
                     'echo "/opt/fluent-bit/bin/fluent-bit") '
                     f'-c {cfg_path} > /tmp/fluentbit.log 2>&1 &')
        return f'set -e; {install_cmd}; {config_cmd}; {start_cmd}'

    def fluentbit_config(self,
                         cluster_name: resources_utils.ClusterName) -> str:
        cfg_dict = {
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
                'outputs': [self.fluentbit_output_config(cluster_name)],
            }
        }
        return yaml_utils.dump_yaml_str(cfg_dict)

    @abc.abstractmethod
    def fluentbit_output_config(
            self, cluster_name: resources_utils.ClusterName) -> Dict[str, Any]:
        pass
