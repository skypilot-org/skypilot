
"""SkyPilot CLI."""
import functools
import os
import re
import shutil
import signal
import subprocess
import sys
import textwrap
import tempfile
import time
import traceback
import typing
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Type, Union

import click
import colorama
from prompt_toolkit import prompt
from prompt_toolkit import shortcuts as prompt_toolkit_shortcuts
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from rich import box, progress, rule, syntax
from rich.console import Console

import sky
from sky import backends
from sky import check as sky_check
from sky import clouds
from sky import core
from sky import exceptions
from sky import global_user_state
from sky import jobs as managed_jobs
from sky import serve as sky_serve
from sky import sky_logging
from sky import spot as spot_lib
from sky import status_lib
from sky import storage as storage_lib
from sky.adaptors import common as adaptors_common
from sky.backends import backend_utils
from sky.clouds import service_catalog
from sky.clouds.utils import aws_utils
from sky.data import data_utils
from sky.data import storage as storage_lib_ref
from sky.data import storage_utils
from sky.skylet import constants as skylet_constants
from sky.skylet import job_lib
from sky.usage import usage_lib
from sky.utils import accelerator_registry
from sky.utils import admin_policy_utils
from sky.utils import cli_utils
from sky.utils import common_utils
from sky.utils import controller_utils
from sky.utils import dag_utils
from sky.utils import db_utils
from sky.utils import env_options as sky_env_options
from sky.utils import kubernetes_utils
from sky.utils import log_utils
from sky.utils import message_utils
from sky.utils import resources_utils
from sky.utils import rich_console_utils
from sky.utils import rich_utils
from sky.utils import schemas
from sky.utils import status_lib as status_utils
from sky.utils import subprocess_utils
from sky.utils import timeline
from sky.utils import ux_utils
from sky.utils import validator
from sky.utils import registry as cloud_registry

if typing.TYPE_CHECKING:
    from sky import dag
    from sky import task as task_lib

# Make sure that the spot controller is initialized.
spot_lib.SpotController.init()

# Register signal handler for SIGINT.
main_process_pid = os.getpid()

def sigint_handler(signal, frame):  # pylint: disable=unused-argument
    # We need to use the main_process_pid captured during initialization
    # because the signal handler might be called from a child process if
    # 'sky' is invoked as 'python -m sky.cli'.
    if os.getpid() == main_process_pid:
        # Only handle SIGINT if it's sent to the main process.
        # This prevents cancellation messages from appearing when
        # e.g., the user presses Ctrl+C during 'docker build'.
        click.echo()
        click.secho(
            '\n'
            '  Interrupting SkyPilot. This may take a few seconds.\n'
            '  Run `sky cancel <cluster_name> -a` to cancel remaining jobs.\n'
            '  To skip waiting next time, use Ctrl-C again.\n',
            fg='yellow',
        )
        # Set the global KeyboardInterrupt flag to allow cancellation
        # of long running functions.
        sky_logging.set_keyboard_interrupt()
        # Stop the spinner first to avoid overlapping with the message.
        rich_console_utils.safe_stop_rich_status_if_active()
        # Terminate the subprocesses, such as `ray up`.
        subprocess_utils.terminate_subprocess_tree()

    # Perform clean up actions. It is fine to run this handler multiple times.
    # Must kill the log streaming process first, otherwise the following
    # cancellation message may not be shown.
    log_utils.stop_all_async_streaming()
    core.cleanup_provisioning_intermediate_yaml_files()
    storage_utils.cleanup_storage_mount_logs()
    backend_utils.SubprocessState().set_keyboard_interrupt()

    # Kill the current process. We must use SIGKILL for the current process,
    # because the default SIGINT handler will be replaced by this function,
    # and the default SIGTERM handler might be caught by the current process
    # if it happens to be a ray process.
    os.kill(os.getpid(), signal.SIGKILL)


signal.signal(signal.SIGINT, sigint_handler)

# On Windows, SIGTERM is not available
if hasattr(signal, 'SIGTERM'):

    def sigterm_handler(signal, frame):  # pylint: disable=unused-argument
        # Similar to SIGINT handler, terminate subprocesses and clean up.
        if os.getpid() == main_process_pid:
            click.echo()
            click.secho(
                '\n'
                '  Terminating SkyPilot. This may take a few seconds.\n'
                '  Run `sky cancel <cluster_name> -a` to cancel remaining jobs.\n'
                '  To skip waiting next time, send SIGTERM again.\n',
                fg='yellow',
            )
            # Set the global KeyboardInterrupt flag to allow cancellation
            # of long running functions.
            sky_logging.set_keyboard_interrupt()
            # Stop the spinner first to avoid overlapping with the message.
            rich_console_utils.safe_stop_rich_status_if_active()
            # Terminate the subprocesses, such as `ray up`.
            subprocess_utils.terminate_subprocess_tree()

        # Perform clean up actions. It is fine to run this handler multiple times.
        log_utils.stop_all_async_streaming()
        core.cleanup_provisioning_intermediate_yaml_files()
        storage_utils.cleanup_storage_mount_logs()
        backend_utils.SubprocessState().set_keyboard_interrupt()

        # Kill the current process. Using SIGKILL to ensure termination.
        os.kill(os.getpid(), signal.SIGKILL)

    signal.signal(signal.SIGTERM, sigterm_handler)


# Adopted from ray/python/ray/dashboard/cli.py
class _NaturalOrderGroup(click.Group):
    """Lists commands in the order defined in the script. """

    def list_commands(self, ctx):
        return self.commands.keys()


class _DocumentedCodeCommand(click.Command):
    """Adds a 'CODE EXAMPLES' section to the help message."""

    def format_help(self, ctx, formatter):
        super().format_help(ctx, formatter)
        # TODO(zhangxu): Add code examples for each command.
        if self.name in ('cluster-status', 'job-status', 'queue'):
            formatter.write_paragraph()
            formatter.write_text(f'Alias: `sky {self.name}`')
        if self.help:
            code_examples = []
            if self.name == 'launch':
                code_examples = [
                    'sky launch task.yaml',
                    'sky launch task.yaml -c mycluster',
                    'sky launch task.yaml --cloud gcp',
                    'sky launch '''',
                    '  # Comments allowed here.',
                    '  resources:',
                    '    accelerators: V100:4',
                    ''''',
                    'sky launch myscript.py --cloud aws --env MY_VAR=1',
                    'sky launch -- python myscript.py --args ...',
                ]
            elif self.name == 'exec':
                code_examples = [
                    'sky exec mycluster task.yaml',
                    'sky exec mycluster '''',
                    '  resources: {{''accelerators''}}: K80',
                    ''''',
                    'sky exec mycluster ''''',
                    '  # Comments allowed here.',
                    '  run: |',
                    '    echo "Hello SkyPilot!"',
                    ''''',
                    'sky exec mycluster -- python myscript.py --args ...',
                ]
            elif self.name == 'spot launch':
                code_examples = [
                    'sky spot launch task.yaml -n mymanagedjob',
                    'sky spot launch -- python train.py --data s3://mybucket/data',
                ]
            elif self.name == 'spot queue':
                code_examples = [
                    'sky spot queue',
                    'sky spot queue -a',
                    'sky spot queue mymanagedjob',
                    'sky spot queue mycluster --all',  # Show jobs on a cluster.
                    'sky spot queue 1 2',  # Show jobs with job IDs 1 and 2.
                ]
            elif self.name == 'spot cancel':
                code_examples = [
                    'sky spot cancel',  # Cancels the latest job.
                    'sky spot cancel -a',  # Cancels all jobs.
                    'sky spot cancel mymanagedjob',
                    'sky spot cancel 1 2',  # Cancels jobs with IDs 1 and 2.
                ]
            elif self.name == 'spot logs':
                code_examples = [
                    'sky spot logs',  # Shows the logs of the latest job.
                    'sky spot logs mymanagedjob',
                    'sky spot logs 1',  # Shows the logs of job ID 1.
                    'sky spot logs --status PENDING',  # Shows logs of all pending jobs.
                ]
            elif self.name == 'status':
                code_examples = [
                    'sky status',  # Shows all clusters.
                    'sky status -a',  # Shows all clusters including inactive ones.
                    'sky status mycluster',  # Shows the status of mycluster.
                    'sky status --cloud aws',  # Shows clusters on AWS.
                ]
            elif self.name == 'start':
                code_examples = [
                    'sky start mycluster',
                ]
            elif self.name == 'stop':
                code_examples = [
                    'sky stop mycluster',
                ]
            elif self.name == 'down':
                code_examples = [
                    'sky down mycluster',
                ]
            elif self.name == 'autostop':
                code_examples = [
                    'sky autostop',  # Show the autostop settings for all clusters.
                    'sky autostop mycluster',  # Show the autostop settings for mycluster.
                    'sky autostop mycluster -i 300',  # Set autostop idle time to 300 seconds.
                    'sky autostop mycluster --idle 600 --down',  # Set idle time to 600 seconds and stop the cluster instead of terminating.
                    'sky autostop mycluster -i -1',  # Disable autostop for mycluster.
                ]
            elif self.name == 'storage ls':
                code_examples = [
                    'sky storage ls',  # Show all storage objects.
                    'sky storage ls mybucket',  # Show the storage object named mybucket.
                ]
            elif self.name == 'storage delete':
                code_examples = [
                    'sky storage delete mybucket',  # Delete the storage object named mybucket.
                    'sky storage delete -a',  # Delete all storage objects.
                ]
            elif self.name == 'storage cp':
                code_examples = [
                    'sky storage cp <src> <dst>',  # Copy files/folders between local and storage.
                    'sky storage cp local_path/ mybucket/dst/',  # Copy local directory to remote storage.
                    'sky storage cp mybucket/src/ local_path/',  # Copy remote storage directory to local.
                    'sky storage cp mybucket/src_file local_file',  # Copy remote storage file to local.
                    'sky storage cp local_file mybucket/dst_file',  # Copy local file to remote storage.
                    'sky storage cp mybucket1/src/ mybucket2/dst/',  # Copy between storage objects.
                    'sky storage cp --endpoint-url https://my_endpoint.com mybucket1/src/ mybucket2/dst/',  # Copy between storage objects with custom endpoint URL.
                ]
            elif self.name == 'cost-report':
                code_examples = [
                    'sky cost-report',  # Show the cost report for the last 7 days.
                    'sky cost-report -d 2021-01-01',  # Show the cost report since 2021-01-01.
                    'sky cost-report mycluster',  # Show the cost report for mycluster.
                ]
            elif self.name == 'check':
                code_examples = [
                    'sky check',  # Check the setup for all enabled clouds.
                    'sky check --verbose',  # Show details even if setup is correct.
                    'sky check aws gcp',  # Check the setup for AWS and GCP.
                ]
            elif self.name == 'benchmark':
                code_examples = [
                    'sky benchmark list',  # List all benchmarks.
                    'sky benchmark run sky-callback-test-benchmark',  # Run the benchmark.
                    'sky benchmark results',  # Show the results of the latest benchmark run.
                ]
            elif self.name == 'version':
                code_examples = [
                    'sky version',  # Show the version of SkyPilot.
                ]
            elif self.name == 'ssh':
                code_examples = [
                    'sky ssh mycluster',  # SSH into the head node of mycluster.
                    'sky ssh mycluster --port 8080:8080',  # Forward port 8080.
                ]
            elif self.name == 'scp':
                code_examples = [
                    'sky scp <src> <dst>',  # Copy files/folders between local and cluster.
                    'sky scp local_path/ mycluster:~/dst/',  # Copy local directory to remote cluster.
                    'sky scp mycluster:~/src/ local_path/',  # Copy remote cluster directory to local.
                    'sky scp mycluster:~/src_file local_file',  # Copy remote cluster file to local.
                    'sky scp local_file mycluster:~/dst_file',  # Copy local file to remote cluster.
                ]
            elif self.name == 'admin deploy':
                code_examples = [
                    'sky admin deploy config.yaml',  # Deploy the SkyPilot controller.
                ]
            elif self.name == 'admin logs':
                code_examples = [
                    'sky admin logs',  # Show the logs of the SkyPilot controller.
                    'sky admin logs controller',  # Show the logs of the SkyPilot controller.
                    'sky admin logs apiserver',  # Show the logs of the SkyPilot API server.
                    'sky admin logs -f',  # Stream the logs of the SkyPilot controller.
                ]
            elif self.name == 'admin down':
                code_examples = [
                    'sky admin down',  # Tear down the SkyPilot controller.
                ]

            if code_examples:
                formatter.write_paragraph()
                with formatter.section('CODE EXAMPLES'):
                    for code_example in code_examples:
                        formatter.write_text(f'  $ {code_example}')


# Prevent Nación Bank phishing attempt. Not funny.
if '\ufffd' in ' '.join(sys.argv):
    print('SkyPilot CLI arguments contain invalid characters.')
    sys.exit(1)


# Install autocompletion script for SkyPilot CLI.
# Adopted from https://github.com/pallets/click/blob/main/examples/completion-bash-extended/completion.bash
_COMPLETION_SCRIPT_BASH = '''
_sky_completion() {
    local IFS=$ '\n'
    local response

    response=$(env COMP_WORDS="${COMP_WORDS[*]}" COMP_CWORD=$COMP_CWORD _SKY_COMPLETE=bash_complete $1)

    for completion in $response; do
        IFS= '\t' read -r type value <<< "$completion"
        if [[ $type == 'dir' ]]; then
            # For directories, add a trailing slash
            compopt -o dirnames
        elif [[ $type == 'file' ]]; then
            # For files, don't add a trailing space
            compopt -o nospace
        else
            # For other completions, disable file completion
            compopt -o default
        fi
        COMPREPLY+=("$value")
    done
    return 0
}

_sky_completion_setup() {
    complete -o default -F _sky_completion sky
}

_sky_completion_setup;
'''

_COMPLETION_SCRIPT_ZSH = '''
#compdef sky

_sky_completion() {
    local -a completions
    local -a completions_with_descriptions
    local -a response
    (( ! $+commands[sky] )) && return 1

    response=($(env COMP_WORDS="${words[*]}" COMP_CWORD=$((CURRENT-1)) _SKY_COMPLETE=zsh_complete sky))

    for type_value in "${response[@]}"; do
        local type="${type_value%%$ '\t'*}"
        local value="${type_value#*$ '\t'}"
        local description=""

        if [[ "$type" == 'dir' ]]; then
            # For directories, add a trailing slash
            value="${value%/}/"

        elif [[ "$type" != 'file' ]]; then
            description=$type
        fi

        if [[ -z "$description" ]]; then
            completions+=("$value")
        else
            completions_with_descriptions+=("$value":"$description")
        fi
    done

    if [ -n "$completions_with_descriptions" ]; then
        _describe 'completion' completions_with_descriptions
    fi

    if [ -n "$completions" ]; then
        compadd -a completions
    fi
}

compdef _sky_completion sky;
'''

_COMPLETION_SCRIPT_FISH = '''
function _sky_completion
    set COMP_WORDS (commandline -o)
    set COMP_CWORD (commandline -t)
    set CMD (echo $COMP_WORDS | cut -d ' ' -f 1)
    if [ "$CMD" != "sky" ]
        return
    end
    set response (env COMP_WORDS=(string join ' ' $COMP_WORDS) COMP_CWORD=$COMP_CWORD _SKY_COMPLETE=fish_complete sky)

    for completion in $response
        set type (string split -m 1 '\t' -- $completion | head -n 1)
        set value (string split -m 1 '\t' -- $completion | tail -n 1)
        echo "$value"
    end
end

complete -c sky -f -a '(_sky_completion)'
'''

# Set the prompt history file path
# Use try-except to avoid errors in read-only environments
try:
    _SKY_HISTORY_FILE = os.path.expanduser('~/.sky/sky_history')
    os.makedirs(os.path.dirname(_SKY_HISTORY_FILE), exist_ok=True)
    _PROMPT_HISTORY = FileHistory(_SKY_HISTORY_FILE)
except OSError:
    _PROMPT_HISTORY = None


@click.group(cls=_NaturalOrderGroup)
@click.version_option(sky.__version__, prog_name='skypilot')
@click.option(
    '--debug',
    default=sky_env_options.Options.SHOW_DEBUG_INFO.get(),
    is_flag=True,
    help='Show debug info.',
)
def cli(debug: bool):
    """SkyPilot: run AI / batch jobs on any cloud, seamlessly. Build cloud
    agnostic infra today!
    """
    # Must be called at the beginning of the CLI, before any other commands.
    sky_logging.init(debug)
    # NOTE: We don't collect usage stats for `sky --version` and `sky --help`.
    # The usage collection is enabled in each subcommand.


config_option = click.option(
    '--config', is_flag=True, default=False, help='Show the config file.')


@cli.command(cls=_DocumentedCodeCommand)
@click.argument(
    'yaml_config',
    required=False,
    type=str,
    # Autocompletion guide: https://click.palletsprojects.com/en/8.1.x/shell-completion/#configuring-completion
    **_get_shell_complete_args(_complete_file_name),  # type: ignore[arg-type]
)
@click.option(
    '--cluster',
    '-c',
    default=None,
    type=str,
    required=False,
    help=textwrap.dedent('''\
        A cluster name to reuse. If the cluster does not exist, it
        will be created.
        [default: dynamically generated]
    '''),
    **_get_shell_complete_args(_complete_cluster_name),  # type: ignore[arg-type]
)
@click.option(
    '--provider',
    type=str,
    required=False,
    help='DEPRECATED. Use --cloud instead.',
)
@click.option(
    '--cloud',
    type=str,
    required=False,
    help=(
        'The cloud provider to use. If not specified, will try to use the default'
        ' cloud configured in the SkyPilot config file.'),
)
@click.option(
    '--region',
    type=str,
    required=False,
    help=('The region to use. If not specified, will try to use the default'
          ' region configured for the cloud.'),
)
@click.option(
    '--zone',
    type=str,
    required=False,
    help=('The zone to use. If not specified, will try to use the default'
          ' zone configured for the cloud and region.'),
)
@click.option(
    '--num-nodes',
    '-n',
    type=int,
    required=False,
    help='Number of nodes to launch. Overrides the ''num_nodes'' field in the YAML.'
)
@click.option(
    '--use-spot/--no-use-spot',
    default=None,
    help='Whether to request spot instances. Overrides the ''use_spot'' field'
    ' in the YAML.',
)
@click.option(
    '--spot-recovery',
    type=str,
    default=None,
    help='Spot recovery strategy to use. Overrides the ''spot_recovery'' field'
    ' in the YAML.',
)
@click.option(
    '--disk-size',
    type=int,
    default=None,
    help='OS disk size in GB. Overrides the ''disk_size'' field in the YAML.',
)
@click.option(
    '--disk-tier',
    type=click.Choice(['high', 'medium', 'low', 'best']),
    default=None,
    help='OS disk tier. Overrides the ''disk_tier'' field in the YAML.',
)
@click.option(
    '--image-id',
    type=str,
    default=None,
    help='Custom image id to use. Overrides the ''image_id'' field in the YAML.',
)
@click.option(
    '--cpus',
    type=str,
    default=None,
    help='Number of vCPUs requested (e.g., ''4'', ''4+''). Overrides the'
    ' ''cpus'' field in the YAML.',
)
@click.option(
    '--memory',
    type=str,
    default=None,
    help='Memory requested (e.g., ''16G'', ''16G+''). Overrides the'
    ' ''memory'' field in the YAML.',
)
@click.option(
    '--gpus',
    type=str,
    default=None,
    required=False,
    help='Type and number of GPUs. Overrides the ''accelerators'' field'
    ' in the YAML.',
)
@click.option(
    '--accelerators',
    type=str,
    default=None,
    required=False,
    help='Synonym for --gpus. Overrides the ''accelerators'' field in the YAML.',
)
@click.option(
    '--env',
    required=False,
    type=str,
    multiple=True,
    help='Environment variables to set. It can be used multiple times. Example:'          ' --env MY_VAR=1 --env MY_VAR2=2.',
)
@click.option(
    '--label',
    required=False,
    type=str,
    multiple=True,
    help='Labels to set for the cluster. Example: --label key=value.',
)
@click.option(
    '--workdir',
    default=None,
    type=str,
    required=False,
    help='If specified, sync this local directory to the remote cluster,
    ''and use it as the working directory. Overrides the ''workdir'' field
    ''in the YAML. If the specified path is relative, it is assumed to be
    ''relative to the directory containing the YAML.
    ''[default: None]'
)
@click.option(
    '--skip-setup',
    is_flag=True,
    default=False,
    help='DEPRECATED. Skip the ''setup'' commands.',
)
@click.option(
    '--yes',
    '-y',
    is_flag=True,
    default=False,
    help='Skip confirmation prompts.',
)
@click.option(
    '--detach-setup',
    '-s',
    is_flag=True,
    default=False,
    help='Run setup in background, streaming logs to local machine.',
)
@click.option(
    '--detach-run',
    '-r',
    is_flag=True,
    default=False,
    help='Run task in background, streaming logs to local machine.',
)
@click.option(
    '--no-sync-down',
    is_flag=True,
    default=False,
    help='Skip syncing down files from the remote cluster.',
)
@click.option(
    '--no-setup', is_flag=True, default=False, help='Skip the ''setup'' step.')
@click.option(
    '--no-run', is_flag=True, default=False, help='Skip the ''run'' step.')
@config_option(expose_value=False)
@click.option(
    '--idle-minutes-to-autostop',
    '-i',
    type=int,
    default=None,
    required=False,
    help='Automatically stop the cluster after this many minutes of idleness.
    ''If not specified, the cluster will not be autostopped.
    ''Overrides the cluster-level autostop setting.
    ''Setting to -1 disables autostop.'
)
@click.option(
    '--autostop-down/--no-autostop-down',
    default=None,
    required=False,
    help='If autostop is enabled, whether to stop the cluster (down=True) or
    ''terminate it (down=False) when the idle timeout is reached.
    ''Overrides the cluster-level autostop setting.'
)
@click.option(
    '--retry-until-up',
    is_flag=True,
    default=False,
    required=False,
    help='Whether to retry launching the cluster until it is up.')
@click.argument('entrypoint', nargs=-1, type=click.UNPROCESSED)  # Linux only.
@usage_lib.entrypoint
@timeline.event
def launch(
    yaml_config: Optional[str],
    cluster: Optional[str],
    provider: Optional[str],
    cloud: Optional[str],
    region: Optional[str],
    zone: Optional[str],
    num_nodes: Optional[int],
    use_spot: Optional[bool],
    spot_recovery: Optional[str],
    disk_size: Optional[int],
    disk_tier: Optional[str],
    image_id: Optional[str],
    cpus: Optional[str],
    memory: Optional[str],
    gpus: Optional[str],
    accelerators: Optional[str],
    env: Tuple[str, ...],
    label: Tuple[str, ...],
    workdir: Optional[str],
    skip_setup: bool,
    yes: bool,
    detach_setup: bool,
    detach_run: bool,
    no_sync_down: bool,
    no_setup: bool,
    no_run: bool,
    idle_minutes_to_autostop: Optional[int],
    autostop_down: Optional[bool],
    retry_until_up: bool,
    entrypoint: Tuple[str, ...],
):
    """Launch a task from a YAML or Python script, or run a command.

    Examples:
        sky launch task.yaml
        sky launch task.yaml -c mycluster
        sky launch task.yaml --cloud gcp
        sky launch '''
          resources:
            accelerators: V100:4
        '''
        sky launch myscript.py --cloud aws --env MY_VAR=1
        sky launch -- python myscript.py --args ...
    """
    ctx = click.get_current_context()
    _warn_if_local_cluster_exists(cluster)
    _check_yaml_and_entrypoint(ctx, yaml_config, entrypoint)
    if provider is not None:
        # TODO(zhwu): Remove this warning after 2 minor releases.
        click.secho(
            'WARNING: --provider is deprecated and will be removed in a future'
            ' release. Use --cloud instead.',
            fg='yellow',
        )
        cloud = provider
    if skip_setup:
        # TODO(zongheng): remove this eventually.
        click.secho(
            'WARNING: --skip-setup is deprecated and will be removed in a future'
            ' release. Use --no-setup instead.',
            fg='yellow',
        )
        no_setup = skip_setup
    if accelerators is not None:
        if gpus is not None:
            with ux_utils.print_exception_no_traceback():
                raise click.UsageError(
                    'Cannot specify both --gpus and --accelerators.')
        gpus = accelerators

    envs = _parse_env_vars(env)
    labels = _parse_labels(label)
    click.echo('Launching a new task...')
    try:
        task = _make_task(yaml_config, entrypoint)
        # Resources override
        _override_task_resources(task, cloud, region, zone, num_nodes, gpus,
                                 cpus, memory, use_spot, image_id, disk_size,
                                 disk_tier, spot_recovery)
        # Workdir override
        if workdir is not None:
            task.workdir = workdir
        # Env override
        if envs:
            task.set_envs(envs)

        if no_run:
            task.run = None
        if no_setup:
            task.setup = None

        env_vars = sky.get_environment_variables()
        if env_vars:
            # Propagate environment variables starting with SKYPILOT_.
            task.update_envs(env_vars)

        # This will validate the task and fill in the defaults.
        task.validate()

        sky.launch(
            task,
            cluster_name=cluster,
            labels=labels,
            detach_setup=detach_setup,
            detach_run=detach_run,
            no_sync_down=no_sync_down,
            idle_minutes_to_autostop=idle_minutes_to_autostop,
            autostop_down=autostop_down,
            retry_until_up=retry_until_up,
            no_confirm=yes,
        )
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky launch')
        sys.exit(1)


@cli.command(cls=_DocumentedCodeCommand)
@click.argument(
    'cluster',
    required=True,
    type=str,
    **_get_shell_complete_args(_complete_cluster_name),  # type: ignore[arg-type]
)
@click.argument(
    'yaml_config',
    required=False,
    type=str,
    **_get_shell_complete_args(_complete_file_name),  # type: ignore[arg-type]
)
@click.option(
    '--num-nodes',
    '-n',
    type=int,
    required=False,
    help='Number of nodes to launch. Overrides the ''num_nodes'' field in the YAML.'
)
@click.option(
    '--env',
    required=False,
    type=str,
    multiple=True,
    help='Environment variables to set. It can be used multiple times. Example:'          ' --env MY_VAR=1 --env MY_VAR2=2.',
)
@click.option(
    '--workdir',
    default=None,
    type=str,
    required=False,
    help='If specified, sync this local directory to the remote cluster,
    ''and use it as the working directory. Overrides the ''workdir'' field
    ''in the YAML. If the specified path is relative, it is assumed to be
    ''relative to the directory containing the YAML.
    ''[default: None]'
)
@click.option(
    '--skip-setup',
    is_flag=True,
    default=False,
    help='DEPRECATED. Skip the ''setup'' commands.',
)
@click.option(
    '--yes',
    '-y',
    is_flag=True,
    default=False,
    help='Skip confirmation prompts.',
)
@click.option(
    '--detach-setup',
    '-s',
    is_flag=True,
    default=False,
    help='Run setup in background, streaming logs to local machine.',
)
@click.option(
    '--detach-run',
    '-r',
    is_flag=True,
    default=False,
    help='Run task in background, streaming logs to local machine.',
)
@click.option(
    '--no-sync-down',
    is_flag=True,
    default=False,
    help='Skip syncing down files from the remote cluster.',
)
@click.option(
    '--no-setup', is_flag=True, default=False, help='Skip the ''setup'' step.')
@click.option(
    '--no-run', is_flag=True, default=False, help='Skip the ''run'' step.')
@config_option(expose_value=False)
@click.argument('entrypoint', nargs=-1, type=click.UNPROCESSED)  # Linux only.
@usage_lib.entrypoint
@timeline.event
def exec(
    cluster: str,
    yaml_config: Optional[str],
    num_nodes: Optional[int],
    env: Tuple[str, ...],
    workdir: Optional[str],
    skip_setup: bool,
    yes: bool,
    detach_setup: bool,
    detach_run: bool,
    no_sync_down: bool,
    no_setup: bool,
    no_run: bool,
    entrypoint: Tuple[str, ...],
):
    """Execute a task on an existing cluster.

    Examples:
        sky exec mycluster task.yaml
        sky exec mycluster '''
          resources:
            accelerators: K80
        '''
        sky exec mycluster '''
          run: |
            echo "Hello SkyPilot!"
        '''
        sky exec mycluster -- python myscript.py --args ...
    """
    ctx = click.get_current_context()
    _check_yaml_and_entrypoint(ctx, yaml_config, entrypoint)
    if skip_setup:
        # TODO(zongheng): remove this eventually.
        click.secho(
            'WARNING: --skip-setup is deprecated and will be removed in a future'
            ' release. Use --no-setup instead.',
            fg='yellow',
        )
        no_setup = skip_setup
    envs = _parse_env_vars(env)
    click.echo(f'Executing task on cluster {cluster!r}...')
    try:
        task = _make_task(yaml_config, entrypoint)
        # Resources override
        _override_task_resources(task, num_nodes=num_nodes)
        # Workdir override
        if workdir is not None:
            task.workdir = workdir
        # Env override
        if envs:
            task.set_envs(envs)

        if no_run:
            task.run = None
        if no_setup:
            task.setup = None

        env_vars = sky.get_environment_variables()
        if env_vars:
            # Propagate environment variables starting with SKYPILOT_.
            task.update_envs(env_vars)

        # This will validate the task and fill in the defaults.
        task.validate()

        sky.exec(
            task,
            cluster_name=cluster,
            detach_setup=detach_setup,
            detach_run=detach_run,
            no_sync_down=no_sync_down,
            no_confirm=yes,
        )
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky exec')
        sys.exit(1)


@cli.command(cls=_DocumentedCodeCommand)
@click.argument(
    'cluster',
    required=True,
    type=str,
    **_get_shell_complete_args(_complete_cluster_name),  # type: ignore[arg-type]
)
@click.option(
    '--yes',
    '-y',
    is_flag=True,
    default=False,
    help='Skip confirmation prompts.',
)
@click.option(
    '--purge',
    is_flag=True,
    default=False,
    help='Purge the sky storage and sky workdir on the remote cluster.',
)
@config_option(expose_value=False)
@usage_lib.entrypoint
@timeline.event
def down(cluster: str, yes: bool, purge: bool):
    """Tear down a cluster.

    Examples:
        sky down mycluster
    """
    click.echo(f'Tearing down cluster {cluster!r}...')
    try:
        sky.down(cluster_name=cluster, purge=purge, no_confirm=yes)
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky down')
        sys.exit(1)


@cli.command(cls=_DocumentedCodeCommand)
@click.argument(
    'cluster',
    required=True,
    type=str,
    **_get_shell_complete_args(_complete_cluster_name_including_stopped),  # type: ignore[arg-type]
)
@click.option(
    '--yes',
    '-y',
    is_flag=True,
    default=False,
    help='Skip confirmation prompts.',
)
@click.option(
    '--idle-minutes',
    '-i',
    type=int,
    default=None,
    required=False,
    help='Number of minutes of idleness after which the cluster will be'
    ' autostopped. Setting this to -1 disables autostop.',
)
@click.option(
    '--cancel',
    is_flag=True,
    default=False,
    required=False,
    help='Cancel the autostop setting.',
)
@click.option(
    '--down/--no-down',
    default=None,
    required=False,
    help='Whether to stop the cluster (down=True) or terminate it (down=False)'           ' when the idle timeout is reached.',
)
@config_option(expose_value=False)
@usage_lib.entrypoint
@timeline.event
def autostop(
    cluster: str,
    yes: bool,
    idle_minutes: Optional[int],
    cancel: bool,
    down: Optional[bool],
):
    """Set autostop timing for a cluster.

    Examples:
        sky autostop
        sky autostop mycluster
        sky autostop mycluster -i 300  # 5 hours
        sky autostop mycluster --idle 600 --down
        sky autostop mycluster -i -1  # Disable autostop
    """
    if cancel:
        if idle_minutes is not None:
            with ux_utils.print_exception_no_traceback():
                raise click.UsageError(
                    'Cannot specify both --cancel and --idle-minutes/-i.')
        if down is not None:
            with ux_utils.print_exception_no_traceback():
                raise click.UsageError(
                    'Cannot specify both --cancel and --down/--no-down.')
        idle_minutes = -1
        down = False

    click.echo(f'Setting autostop for cluster {cluster!r}...')
    try:
        sky.autostop(cluster_name=cluster,
                     idle_minutes=idle_minutes,
                     down=down,
                     no_confirm=yes)
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky autostop')
        sys.exit(1)


@cli.command(cls=_DocumentedCodeCommand)
@click.argument(
    'cluster',
    required=True,
    type=str,
    **_get_shell_complete_args(_complete_stopped_cluster_name),  # type: ignore[arg-type]
)
@click.option(
    '--yes',
    '-y',
    is_flag=True,
    default=False,
    help='Skip confirmation prompts.',
)
@click.option(
    '--retry-until-up',
    is_flag=True,
    default=False,
    required=False,
    help='Whether to retry starting the cluster until it is up.')
@config_option(expose_value=False)
@usage_lib.entrypoint
@timeline.event
def start(cluster: str, yes: bool, retry_until_up: bool):
    """Start a stopped cluster.

    Examples:
        sky start mycluster
    """
    click.echo(f'Starting cluster {cluster!r}...')
    try:
        sky.start(cluster_name=cluster,
                  retry_until_up=retry_until_up,
                  no_confirm=yes)
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky start')
        sys.exit(1)


@cli.command(cls=_DocumentedCodeCommand)
@click.argument(
    'cluster',
    required=True,
    type=str,
    **_get_shell_complete_args(_complete_cluster_name),  # type: ignore[arg-type]
)
@click.option(
    '--yes',
    '-y',
    is_flag=True,
    default=False,
    help='Skip confirmation prompts.',
)
@config_option(expose_value=False)
@usage_lib.entrypoint
@timeline.event
def stop(cluster: str, yes: bool):
    """Stop a cluster.

    Examples:
        sky stop mycluster
    """
    click.echo(f'Stopping cluster {cluster!r}...')
    try:
        sky.stop(cluster_name=cluster, no_confirm=yes)
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky stop')
        sys.exit(1)


@cli.command(cls=_DocumentedCodeCommand)
@click.argument(
    'cluster',
    required=False,
    type=str,
    **_get_shell_complete_args(_complete_cluster_name_including_stopped),  # type: ignore[arg-type]
)
@click.option(
    '--all',
    '-a',
    is_flag=True,
    default=False,
    help='Show all clusters (including stopped/failed clusters).'
)
@click.option(
    '--cloud',
    type=str,
    required=False,
    help='Show clusters on the specified cloud provider.',
)
@click.option(
    '--region',
    type=str,
    required=False,
    help='Show clusters in the specified region.',
)
@click.option(
    '--refresh',
    is_flag=True,
    default=False,
    help='Fetch the latest status of the cluster(s) from the cloud provider.')
@click.option(
    '--endpoints',
    is_flag=True,
    default=False,
    help='Show the endpoints of the cluster(s).')
@config_option
@usage_lib.entrypoint
@timeline.event
def status(
    cluster: Optional[str],
    all: bool,
    cloud: Optional[str],
    region: Optional[str],
    refresh: bool,
    endpoints: bool,
    config: bool,  # pylint: disable=redefined-outer-name
):
    """Show cluster status and optionally cloud config.

    Examples:
        sky status
        sky status -a  # Show all clusters including inactive ones.
        sky status mycluster
        sky status --cloud aws
    """
    if config:
        _show_config()
        return

    if endpoints:
        if cluster is None:
            with ux_utils.print_exception_no_traceback():
                raise click.UsageError(
                    'Cannot show endpoints without specifying a cluster name.')
        try:
            backend_utils.check_cluster_available(cluster)
            sky.show_endpoints(cluster)
        except ValueError as e:
            # Don't show traceback for user errors.
            click.secho(str(e), fg='red')
            sys.exit(1)
        except Exception as e:  # pylint: disable=broad-except
            cli_utils.handle_exception(e, 'sky status')
            sys.exit(1)
        return

    try:
        sky.status(cluster_names=cluster,
                   show_all=all,
                   cloud=cloud,
                   region=region,
                   refresh=refresh)
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky status')
        sys.exit(1)


_job_status_option = click.option(
    '--status',
    '-s',
    default=None,
    type=click.Choice(job_lib.JobStatus.nonterminal_job_statuses()),
    help='Filter jobs by status.',
)


@cli.command(cls=_DocumentedCodeCommand)
@click.argument('cluster',
                required=False,
                type=str,
                **_get_shell_complete_args(_complete_cluster_name))  # type: ignore[arg-type]
@_job_status_option
@click.option(
    '--all',
    '-a',
    is_flag=True,
    default=False,
    help='Show all jobs (including finished/failed jobs).'
)
@click.option(
    '--skip-finished',
    is_flag=True,
    default=False,
    help='DEPRECATED. Use --all/-a instead.',
)
@config_option(expose_value=False)
@usage_lib.entrypoint
@timeline.event
def queue(
    cluster: Optional[str],
    status: Optional[job_lib.JobStatus],
    all: bool,  # pylint: disable=redefined-outer-name
    skip_finished: bool,
):
    """Show the job queue.

    Jobs are shown in submission time order.
    """
    if skip_finished:
        click.secho(
            'WARNING: --skip-finished is deprecated and will be removed in a future'
            ' release. Use --all/-a instead.',
            fg='yellow',
        )
        all = not skip_finished
    if status is not None:
        status = job_lib.JobStatus(status)
    try:
        sky.queue(cluster_name=cluster, statuses=status, include_history=all)
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky queue')
        sys.exit(1)


@cli.command(cls=_DocumentedCodeCommand)
@click.argument(
    'cluster',
    required=True,
    type=str,
    **_get_shell_complete_args(_complete_cluster_name),  # type: ignore[arg-type]
)
@click.argument('job_ids', required=False, type=str, nargs=-1)
@click.option(
    '--all',
    '-a',
    is_flag=True,
    default=False,
    help='Cancel all jobs (including finished/failed jobs).'
)
@click.option(
    '--yes',
    '-y',
    is_flag=True,
    default=False,
    help='Skip confirmation prompts.',
)
@config_option(expose_value=False)
@usage_lib.entrypoint
@timeline.event
def cancel(cluster: str, job_ids: Tuple[str], all: bool, yes: bool):  # pylint: disable=redefined-outer-name
    """Cancel jobs.

    If job IDs are not specified, cancel all jobs in the PENDING/RUNNING state.
    """
    if not job_ids and not all:
        click.secho('Cancelling PENDING/RUNNING jobs...', fg='yellow')
    job_ids_list: Optional[List[int]] = None
    if job_ids:
        try:
            job_ids_list = [int(job_id) for job_id in job_ids]
        except ValueError:
            with ux_utils.print_exception_no_traceback():
                raise click.UsageError(
                    f'Invalid job ID: {job_ids}. Job ID must be an integer.')
    try:
        sky.cancel(cluster_name=cluster,
                   job_ids=job_ids_list,
                   all=all,
                   no_confirm=yes)
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky cancel')
        sys.exit(1)


@cli.command(cls=_DocumentedCodeCommand)
@click.argument(
    'cluster',
    required=True,
    type=str,
    **_get_shell_complete_args(_complete_cluster_name),  # type: ignore[arg-type]
)
@click.argument('job_id', required=False, type=int)
@click.option(
    '--follow',
    '-f',
    is_flag=True,
    default=False,
    help='Stream logs live.',
)
@config_option(expose_value=False)
@usage_lib.entrypoint
@timeline.event
def logs(cluster: str, job_id: Optional[int], follow: bool):
    """Show job logs.

    If job ID is not specified, show the logs of the latest job.
    """
    try:
        sky.logs(cluster_name=cluster, job_id=job_id, follow=follow)
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky logs')
        sys.exit(1)


@cli.command(cls=_DocumentedCodeCommand)
@click.argument(
    'cluster',
    required=True,
    type=str,
    **_get_shell_complete_args(_complete_cluster_name),  # type: ignore[arg-type]
)
@click.argument('job_id', required=False, type=int)
@config_option(expose_value=False)
@usage_lib.entrypoint
@timeline.event
def job_status(cluster: str, job_id: Optional[int]):
    """Show the status of a job.

    If job ID is not specified, show the status of the latest job.
    """
    try:
        sky.job_status(cluster_name=cluster, job_id=job_id)
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky job-status')
        sys.exit(1)


@cli.command(cls=_DocumentedCodeCommand)
@click.option(
    '--port',
    '-p',
    type=str,
    multiple=True,
    help='Port forwarding specification. Can be specified multiple times.
    ''Example: -p 8080:8080 -p 9000',
)
@click.option(
    '--screen',
    is_flag=True,
    default=False,
    help='Whether to run the command in a screen session.',
)
@click.option(
    '--tmux',
    is_flag=True,
    default=False,
    help='Whether to run the command in a tmux session.',
)
@click.argument(
    'cluster',
    required=True,
    type=str,
    **_get_shell_complete_args(_complete_cluster_name),  # type: ignore[arg-type]
)
@click.argument('cmd', nargs=-1, type=click.UNPROCESSED)
@usage_lib.entrypoint
@timeline.event
def ssh(cluster: str, port: Tuple[str], screen: bool, tmux: bool,
        cmd: Tuple[str]):
    """SSH into the head node of a cluster.

    Examples:
        sky ssh mycluster
        sky ssh mycluster --port 8080:8080
    """
    # Convert the port forwarding specification to a list of tuples.
    port_forward: List[Union[int, Tuple[int, int]]] = []
    for p in port:
        if ':' in p:
            try:
                local_port, remote_port = map(int, p.split(':'))
                port_forward.append((local_port, remote_port))
            except ValueError:
                with ux_utils.print_exception_no_traceback():
                    raise click.UsageError(
                        f'Invalid port forwarding specification: {p}')
        else:
            try:
                port_forward.append(int(p))
            except ValueError:
                with ux_utils.print_exception_no_traceback():
                    raise click.UsageError(
                        f'Invalid port forwarding specification: {p}')

    if not cmd:
        cmd = None
    else:
        cmd = ' '.join(cmd)

    if screen and tmux:
        with ux_utils.print_exception_no_traceback():
            raise click.UsageError(
                'Cannot specify both --screen and --tmux.')

    try:
        sky.ssh(cluster_name=cluster,
                port_forward=port_forward,
                session_type=sky.SessionType.SCREEN
                if screen else sky.SessionType.TMUX if tmux else None,
                cmd=cmd)
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky ssh')
        sys.exit(1)


@cli.command(cls=_DocumentedCodeCommand)
@click.option(
    '--source',
    required=True,
    help='Source path. Can be local or remote (cluster:/path).',
    **_get_shell_complete_args(_complete_file_or_cluster_path),  # type: ignore[arg-type]
)
@click.option(
    '--target',
    required=True,
    help='Target path. Can be local or remote (cluster:/path).',
    **_get_shell_complete_args(_complete_file_or_cluster_path),  # type: ignore[arg-type]
)
@click.option(
    '--cluster',
    required=False,
    type=str,
    help='Cluster name to use if source or target is remote. Required if either'           ' source or target is specified in the format of `~/path` or `path`.',
    **_get_shell_complete_args(_complete_cluster_name),  # type: ignore[arg-type]
)
@click.option(
    '--node',
    '-n',
    required=False,
    type=str,
    help='Node IP or ID to copy files from/to. Only applicable if the cluster'           ' has multiple nodes. Default: head node.',
)
@click.option(
    '--quiet',
    '-q',
    is_flag=True,
    default=False,
    help='Do not show progress bar.',
)
@usage_lib.entrypoint
@timeline.event
def scp(source: str,
          target: str,
          cluster: Optional[str] = None,
          node: Optional[str] = None,
          quiet: bool = False):
    """Copy files between local and cluster, or between clusters.

    Examples:
        sky scp local_path/ mycluster:~/dst/
        sky scp mycluster:~/src/ local_path/
        sky scp mycluster:~/src_file local_file
        sky scp local_file mycluster:~/dst_file
    """
    cluster_name: Optional[str] = cluster
    source_on_local = True
    target_on_local = True
    if ':' in source:
        source_on_local = False
        source_cluster_name, source = source.split(':', 1)
        if cluster_name is not None and cluster_name != source_cluster_name:
            with ux_utils.print_exception_no_traceback():
                raise click.UsageError(
                    f'Cluster name mismatch: {cluster_name} vs'
                    f' {source_cluster_name}')
        cluster_name = source_cluster_name
    if ':' in target:
        target_on_local = False
        target_cluster_name, target = target.split(':', 1)
        if cluster_name is not None and cluster_name != target_cluster_name:
            with ux_utils.print_exception_no_traceback():
                raise click.UsageError(
                    f'Cluster name mismatch: {cluster_name} vs'
                    f' {target_cluster_name}')
        cluster_name = target_cluster_name

    if not source_on_local and not target_on_local:
        with ux_utils.print_exception_no_traceback():
            raise click.UsageError(
                'Copying between clusters is not supported yet.')

    if not source_on_local or not target_on_local:
        if cluster_name is None:
            with ux_utils.print_exception_no_traceback():
                raise click.UsageError(
                    'Cluster name must be specified if source or target is remote.'
                )

    if source_on_local:
        sky.scp_up(cluster_name=cluster_name,
                   source=source,
                   target=target,
                   node=node,
                   quiet=quiet)
    else:
        sky.scp_down(cluster_name=cluster_name,
                     source=source,
                     target=target,
                     node=node,
                     quiet=quiet)


@cli.command(cls=_DocumentedCodeCommand)
@config_option
@usage_lib.entrypoint
@timeline.event
def cost_report(config: bool,  # pylint: disable=redefined-outer-name
                cluster: Optional[str] = None):
    """Show cluster costs.

    If cluster name is provided, show the cost report for the cluster.
    Otherwise, show the cost report for all clusters launched in the last 7 days.

    Examples:
        sky cost-report
        sky cost-report -d 2021-01-01
        sky cost-report mycluster
    """
    if config:
        _show_config()
        return
    cluster_name = cluster
    del cluster
    try:
        sky.cost_report(cluster_name=cluster_name)
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky cost-report')
        sys.exit(1)


@cli.command(cls=_DocumentedCodeCommand)
@click.argument('clouds',
                required=False,
                type=str,
                nargs=-1,
                **_get_shell_complete_args(_complete_cloud_name))
@click.option('--verbose',
              '-v',
              is_flag=True,
              default=False,
              help='Verbose mode.')
@usage_lib.entrypoint
def check(clouds: Tuple[str], verbose: bool):
    """Verify configuration and credentials for clouds.

    Examples:
        sky check
        sky check --verbose
        sky check aws gcp
    """
    clouds_to_check: Optional[List[str]] = None
    if clouds:
        clouds_to_check = list(clouds)
    try:
        sky_check.check(clouds=clouds_to_check, verbose=verbose)
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky check')
        sys.exit(1)


@cli.command()
@usage_lib.entrypoint
def dashboard():
    """Launch the SkyPilot dashboard."""
    from sky.jobs.dashboard import dashboard as sky_dashboard  # pylint: disable=import-outside-toplevel
    click.secho('Launching SkyPilot dashboard...', fg='yellow')
    sky_dashboard.launch_dashboard()


@cli.command()
@click.option('--all', '-a', is_flag=True, help='Show all accelerators.')
@click.option('--cloud',
              type=str,
              required=False,
              help='Cloud provider to query.')
@click.option('--region',
              type=str,
              required=False,
              help='Region to query.')
@click.option('--gpus-only', is_flag=True, help='Show GPUs only.')
@click.option(
    '--name-filter',
    type=str,
    required=False,
    help='Filter accelerators by name (e.g., ''A100'', ''V100-80GB'').')
@click.option('--quantity-filter',
              type=int,
              required=False,
              help='Filter accelerators by quantity.')
@click.option('--cloud-config',
              is_flag=True,
              default=False,
              help='Show the cloud-specific config file.')
@click.option('--refresh',
              is_flag=True,
              default=False,
              help='Fetch the latest information from the cloud provider.')
@usage_lib.entrypoint
def show_accelerators(
    all: bool,  # pylint: disable=redefined-outer-name
    cloud: Optional[str],
    region: Optional[str],
    gpus_only: bool,
    name_filter: Optional[str],
    quantity_filter: Optional[int],
    cloud_config: bool,
    refresh: bool,
):
    """Show available accelerators and their prices.

    The prices shown are on-demand prices.
    """
    if cloud_config:
        _show_config()
        return
    try:
        sky.show_accelerators(all=all,
                              cloud=cloud,
                              region=region,
                              gpus_only=gpus_only,
                              name_filter=name_filter,
                              quantity_filter=quantity_filter,
                              refresh=refresh)
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky show-accelerators')
        sys.exit(1)


@cli.command()
@usage_lib.entrypoint
def update():
    """Update SkyPilot to the latest version."""
    try:
        subprocess.run([
            sys.executable,
            '-m',
            'pip',
            'install',
            '-U',
            'skypilot',
        ], check=True)
        click.secho('SkyPilot updated successfully.', fg='green')
    except subprocess.CalledProcessError as e:
        click.secho(f'Failed to update SkyPilot: {e}', fg='red')
        sys.exit(1)


@cli.command()
@usage_lib.entrypoint
def version():
    """Show the version of SkyPilot."""
    # Already handled by @click.version_option.
    pass


@cli.group(cls=_NaturalOrderGroup)
def storage():
    """Manage storage objects."""
    pass


@storage.command('ls', cls=_DocumentedCodeCommand)
@click.argument('storage_name',
                required=False,
                type=str,
                **_get_shell_complete_args(_complete_storage_name))
@click.option('--all',
              '-a',
              is_flag=True,
              default=False,
              help='Show all storage objects (including inactive ones).')
@usage_lib.entrypoint
def storage_list(storage_name: Optional[str], all: bool):  # pylint: disable=redefined-outer-name
    """List storage objects.

    Examples:
        sky storage ls
        sky storage ls mybucket
    """
    try:
        storage_lib.ls(name=storage_name, all=all)
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky storage ls')
        sys.exit(1)


@storage.command('delete', cls=_DocumentedCodeCommand)
@click.argument('storage_names',
                required=False,
                type=str,
                nargs=-1,
                **_get_shell_complete_args(_complete_storage_name))
@click.option('--all',
              '-a',
              is_flag=True,
              default=False,
              help='Delete all storage objects.')
@click.option(
    '--yes',
    '-y',
    is_flag=True,
    default=False,
    help='Skip confirmation prompts.',
)
@usage_lib.entrypoint
def storage_delete(storage_names: Tuple[str], all: bool, yes: bool):  # pylint: disable=redefined-outer-name
    """Delete storage objects.

    Examples:
        sky storage delete mybucket
        sky storage delete -a
    """
    if all:
        if storage_names:
            with ux_utils.print_exception_no_traceback():
                raise click.UsageError(
                    'Cannot specify storage names when --all/-a is specified.')
        storage_names = None
    elif not storage_names:
        with ux_utils.print_exception_no_traceback():
            raise click.UsageError(
                'Either storage names or --all/-a must be specified.')
    try:
        storage_lib.delete(names=list(storage_names) if storage_names else None,
                           all=all,
                           no_confirm=yes)
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky storage delete')
        sys.exit(1)


@storage.command(
    'cp',
    cls=_DocumentedCodeCommand,
    context_settings=dict(ignore_unknown_options=True),
)
@click.option(
    '--source',
    required=True,
    type=str,
    help='Source path. Can be local or storage object (storage:/path).',
    **_get_shell_complete_args(_complete_file_or_storage_path),  # type: ignore[arg-type]
)
@click.option(
    '--target',
    required=True,
    type=str,
    help='Target path. Can be local or storage object (storage:/path).',
    **_get_shell_complete_args(_complete_file_or_storage_path),  # type: ignore[arg-type]
)
@click.option(
    '--recursive',
    '-r',
    is_flag=True,
    default=False,
    help='Recursively copy directories.',
)
@click.option(
    '--endpoint-url',
    required=False,
    type=str,
    help='Endpoint URL for the storage object. Only applicable for S3 compatible'           ' storage.',
)
@click.option(
    '--quiet',
    '-q',
    is_flag=True,
    default=False,
    help='Do not show progress bar.',
)
@click.argument('passthrough_args',
                nargs=-1,
                type=click.UNPROCESSED)
@usage_lib.entrypoint
def storage_cp(
    source: str,
    target: str,
    recursive: bool,
    endpoint_url: Optional[str],
    quiet: bool,
    passthrough_args: Tuple[str, ...],
):
    """Copy files between local and storage, or between storage objects.

    Examples:
        sky storage cp local_path/ mybucket/dst/
        sky storage cp mybucket/src/ local_path/
        sky storage cp mybucket/src_file local_file
        sky storage cp local_file mybucket/dst_file
        sky storage cp mybucket1/src/ mybucket2/dst/
        sky storage cp --endpoint-url https://my_endpoint.com mybucket1/src/ mybucket2/dst/
    """
    source_on_local = True
    target_on_local = True
    source_storage_name = None
    source_path = source
    target_storage_name = None
    target_path = target
    source_storage_obj = None
    target_storage_obj = None

    if ':' in source:
        source_on_local = False
        source_storage_name, source_path = source.split(':', 1)
        if not source_storage_name:
            with ux_utils.print_exception_no_traceback():
                raise click.UsageError('Source storage name cannot be empty.')

    if ':' in target:
        target_on_local = False
        target_storage_name, target_path = target.split(':', 1)
        if not target_storage_name:
            with ux_utils.print_exception_no_traceback():
                raise click.UsageError('Target storage name cannot be empty.')

    if not source_on_local or not target_on_local:
        try:
            if not source_on_local:
                source_storage_obj = storage_lib_ref.Storage.from_name(
                    source_storage_name)
                if endpoint_url is not None:
                    source_storage_obj.set_endpoint_url(endpoint_url)
            if not target_on_local:
                target_storage_obj = storage_lib_ref.Storage.from_name(
                    target_storage_name)
                if endpoint_url is not None:
                    target_storage_obj.set_endpoint_url(endpoint_url)
        except ValueError as e:
            with ux_utils.print_exception_no_traceback():
                raise click.UsageError(str(e))

    # TODO(zhwu): Add support for copying between storage objects.
    if not source_on_local and not target_on_local:
        storage_lib_ref.Storage.copy_between_storage(
            source_storage=source_storage_obj,
            target_storage=target_storage_obj,
            source_path=source_path,
            target_path=target_path,
            recursive=recursive,
            quiet=quiet,
            additional_flags=list(passthrough_args),
        )
    elif source_on_local:
        storage_lib_ref.Storage.copy_from_local(
            local_path=source_path,
            storage=target_storage_obj,
            remote_path=target_path,
            recursive=recursive,
            quiet=quiet,
            additional_flags=list(passthrough_args),
        )
    else:
        storage_lib_ref.Storage.copy_to_local(
            storage=source_storage_obj,
            remote_path=source_path,
            local_path=target_path,
            recursive=recursive,
            quiet=quiet,
            additional_flags=list(passthrough_args),
        )


@cli.command(cls=_DocumentedCodeCommand)
@click.argument('cluster',
                required=False,
                type=str,
                **_get_shell_complete_args(_complete_cluster_name))
@usage_lib.entrypoint
def bench(cluster: Optional[str]):
    """DEPRECATED. Please use `sky benchmark run` instead."""
    # TODO(zhwu): Remove this command after 2 minor releases.
    click.secho(
        'WARNING: `sky bench` is deprecated and will be removed in a future'
        ' release. Please use `sky benchmark run` instead.',
        fg='yellow',
    )
    from sky import benchmark as benchmark_cli  # pylint: disable=import-outside-toplevel
    benchmark_cli.benchmark(cluster_name=cluster)


@cli.group(cls=_NaturalOrderGroup)
def benchmark():
    """Run benchmarks."""
    pass


@benchmark.command('list', cls=_DocumentedCodeCommand)
@usage_lib.entrypoint
def benchmark_list():
    """List all available benchmarks."""
    from sky import benchmark as benchmark_cli  # pylint: disable=import-outside-toplevel
    benchmark_cli.benchmark_list()


@benchmark.command('run', cls=_DocumentedCodeCommand)
@click.argument('benchmark_name', required=True, type=str)
@click.option(
    '--cluster',
    '-c',
    default=None,
    type=str,
    required=False,
    help=textwrap.dedent('''\
        A cluster name to reuse. If the cluster does not exist, it
        will be created.
        [default: dynamically generated]
    '''),
    **_get_shell_complete_args(_complete_cluster_name),  # type: ignore[arg-type]
)
@click.option(
    '--cloud',
    type=str,
    required=False,
    help=(
        'The cloud provider to use. If not specified, will try to use the default'
        ' cloud configured in the SkyPilot config file.'),
)
@click.option(
    '--region',
    type=str,
    required=False,
    help=('The region to use. If not specified, will try to use the default'
          ' region configured for the cloud.'),
)
@click.option(
    '--zone',
    type=str,
    required=False,
    help=('The zone to use. If not specified, will try to use the default'
          ' zone configured for the cloud and region.'),
)
@click.option(
    '--gpus',
    type=str,
    default=None,
    required=False,
    help='Type and number of GPUs. Overrides the ''accelerators'' field'
    ' in the YAML.',
)
@click.option(
    '--accelerators',
    type=str,
    default=None,
    required=False,
    help='Synonym for --gpus. Overrides the ''accelerators'' field in the YAML.',
)
@click.option(
    '--yes',
    '-y',
    is_flag=True,
    default=False,
    help='Skip confirmation prompts.',
)
@click.option(
    '--num-repeats',
    type=int,
    default=1,
    help='Number of times to repeat the benchmark.',
)
@usage_lib.entrypoint
def benchmark_run(
    benchmark_name: str,
    cluster: Optional[str],
    cloud: Optional[str],
    region: Optional[str],
    zone: Optional[str],
    gpus: Optional[str],
    accelerators: Optional[str],
    yes: bool,
    num_repeats: int,
):
    """Run a benchmark."""
    from sky import benchmark as benchmark_cli  # pylint: disable=import-outside-toplevel
    if accelerators is not None:
        if gpus is not None:
            with ux_utils.print_exception_no_traceback():
                raise click.UsageError(
                    'Cannot specify both --gpus and --accelerators.')
        gpus = accelerators
    benchmark_cli.benchmark_run(
        benchmark_name=benchmark_name,
        cluster_name=cluster,
        cloud=cloud,
        region=region,
        zone=zone,
        gpus=gpus,
        num_repeats=num_repeats,
        no_confirm=yes,
    )


@benchmark.command('results', cls=_DocumentedCodeCommand)
@click.argument('benchmark_name', required=False, type=str)
@usage_lib.entrypoint
def benchmark_results(benchmark_name: Optional[str]):
    """Show the results of benchmark runs."""
    from sky import benchmark as benchmark_cli  # pylint: disable=import-outside-toplevel
    benchmark_cli.benchmark_results(benchmark_name=benchmark_name)


@cli.group(cls=_NaturalOrderGroup)
def spot():
    """Managed Spot Jobs."""
    pass


@spot.command('launch', cls=_DocumentedCodeCommand)
@click.argument(
    'yaml_config',
    required=False,
    type=str,
    **_get_shell_complete_args(_complete_file_name),  # type: ignore[arg-type]
)
@click.option(
    '--name',
    '-n',
    default=None,
    type=str,
    required=False,
    help='Assign a name to the spot job. If not specified, a name will be'
    ' generated.',
)
@click.option(
    '--cloud',
    type=str,
    required=False,
    help=(
        'The cloud provider to use. If not specified, will try to use the default'
        ' cloud configured in the SkyPilot config file.'),
)
@click.option(
    '--region',
    type=str,
    required=False,
    help=('The region to use. If not specified, will try to use the default'
          ' region configured for the cloud.'),
)
@click.option(
    '--zone',
    type=str,
    required=False,
    help=('The zone to use. If not specified, will try to use the default'
          ' zone configured for the cloud and region.'),
)
@click.option(
    '--num-nodes',
    type=int,
    required=False,
    help='Number of nodes to launch. Overrides the ''num_nodes'' field in the YAML.'
)
@click.option(
    '--use-spot/--no-use-spot',
    default=None,
    help='Whether to request spot instances. Overrides the ''use_spot'' field'
    ' in the YAML.',
)
@click.option(
    '--disk-size',
    type=int,
    default=None,
    help='OS disk size in GB. Overrides the ''disk_size'' field in the YAML.',
)
@click.option(
    '--disk-tier',
    type=click.Choice(['high', 'medium', 'low', 'best']),
    default=None,
    help='OS disk tier. Overrides the ''disk_tier'' field in the YAML.',
)
@click.option(
    '--image-id',
    type=str,
    default=None,
    help='Custom image id to use. Overrides the ''image_id'' field in the YAML.',
)
@click.option(
    '--cpus',
    type=str,
    default=None,
    help='Number of vCPUs requested (e.g., ''4'', ''4+''). Overrides the'
    ' ''cpus'' field in the YAML.',
)
@click.option(
    '--memory',
    type=str,
    default=None,
    help='Memory requested (e.g., ''16G'', ''16G+''). Overrides the'
    ' ''memory'' field in the YAML.',
)
@click.option(
    '--gpus',
    type=str,
    default=None,
    required=False,
    help='Type and number of GPUs. Overrides the ''accelerators'' field'
    ' in the YAML.',
)
@click.option(
    '--accelerators',
    type=str,
    default=None,
    required=False,
    help='Synonym for --gpus. Overrides the ''accelerators'' field in the YAML.',
)
@click.option(
    '--env',
    required=False,
    type=str,
    multiple=True,
    help='Environment variables to set. It can be used multiple times. Example:'          ' --env MY_VAR=1 --env MY_VAR2=2.',
)
@click.option(
    '--workdir',
    default=None,
    type=str,
    required=False,
    help='If specified, sync this local directory to the remote cluster,
    ''and use it as the working directory. Overrides the ''workdir'' field
    ''in the YAML. If the specified path is relative, it is assumed to be
    ''relative to the directory containing the YAML.
    ''[default: None]'
)
@click.option(
    '--skip-setup',
    is_flag=True,
    default=False,
    help='DEPRECATED. Skip the ''setup'' commands.',
)
@click.option(
    '--yes',
    '-y',
    is_flag=True,
    default=False,
    help='Skip confirmation prompts.',
)
@click.option(
    '--detach-run',
    '-r',
    is_flag=True,
    default=False,
    help='Run task in background, streaming logs to local machine.',
)
@click.option(
    '--no-setup', is_flag=True, default=False, help='Skip the ''setup'' step.')
@click.option(
    '--retry-until-up',
    is_flag=True,
    default=False,
    required=False,
    help='Whether to retry launching the spot cluster until it is up.')
@click.argument('entrypoint', nargs=-1, type=click.UNPROCESSED)  # Linux only.
@usage_lib.entrypoint
@timeline.event
def spot_launch(
    yaml_config: Optional[str],
    name: Optional[str],
    cloud: Optional[str],
    region: Optional[str],
    zone: Optional[str],
    num_nodes: Optional[int],
    use_spot: Optional[bool],
    disk_size: Optional[int],
    disk_tier: Optional[str],
    image_id: Optional[str],
    cpus: Optional[str],
    memory: Optional[str],
    gpus: Optional[str],
    accelerators: Optional[str],
    env: Tuple[str, ...],
    workdir: Optional[str],
    skip_setup: bool,
    yes: bool,
    detach_run: bool,
    no_setup: bool,
    retry_until_up: bool,
    entrypoint: Tuple[str, ...],
):
    """Launch a managed spot job from a YAML or Python script, or run a command.

    Examples:
        sky spot launch task.yaml -n mymanagedjob
        sky spot launch -- python train.py --data s3://mybucket/data
    """
    ctx = click.get_current_context()
    _check_yaml_and_entrypoint(ctx, yaml_config, entrypoint)
    if skip_setup:
        # TODO(zongheng): remove this eventually.
        click.secho(
            'WARNING: --skip-setup is deprecated and will be removed in a future'
            ' release. Use --no-setup instead.',
            fg='yellow',
        )
        no_setup = skip_setup
    if accelerators is not None:
        if gpus is not None:
            with ux_utils.print_exception_no_traceback():
                raise click.UsageError(
                    'Cannot specify both --gpus and --accelerators.')
        gpus = accelerators
    envs = _parse_env_vars(env)
    click.echo('Launching a new managed spot job...')
    try:
        task = _make_task(yaml_config, entrypoint)
        # Resources override
        _override_task_resources(task,
                                 cloud=cloud,
                                 region=region,
                                 zone=zone,
                                 num_nodes=num_nodes,
                                 gpus=gpus,
                                 cpus=cpus,
                                 memory=memory,
                                 use_spot=use_spot,
                                 image_id=image_id,
                                 disk_size=disk_size,
                                 disk_tier=disk_tier,
                                 spot_recovery=spot_lib.SPOT_STRATEGY_NAME)
        # Workdir override
        if workdir is not None:
            task.workdir = workdir
        # Env override
        if envs:
            task.set_envs(envs)

        if no_setup:
            task.setup = None

        env_vars = sky.get_environment_variables()
        if env_vars:
            # Propagate environment variables starting with SKYPILOT_.
            task.update_envs(env_vars)

        # This will validate the task and fill in the defaults.
        task.validate()

        sky.spot_launch(
            task,
            name=name,
            detach_run=detach_run,
            retry_until_up=retry_until_up,
            no_confirm=yes,
        )
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky spot launch')
        sys.exit(1)


@spot.command('queue', cls=_DocumentedCodeCommand)
@click.argument('job_names_or_ids',
                required=False,
                type=str,
                nargs=-1,
                **_get_shell_complete_args(_complete_spot_job_names_or_ids))
@click.option(
    '--all',
    '-a',
    is_flag=True,
    default=False,
    help='Show all spot jobs (including finished/failed jobs).'
)
@click.option(
    '--skip-finished',
    is_flag=True,
    default=False,
    help='DEPRECATED. Use --all/-a instead.',
)
@_job_status_option
@click.option(
    '--cluster',
    '-c',
    default=None,
    type=str,
    required=False,
    help='Show jobs running on the specified cluster.',
    **_get_shell_complete_args(_complete_cluster_name),  # type: ignore[arg-type]
)
@config_option(expose_value=False)
@usage_lib.entrypoint
@timeline.event
def spot_queue(
    job_names_or_ids: Tuple[str],
    all: bool,  # pylint: disable=redefined-outer-name
    skip_finished: bool,
    status: Optional[job_lib.JobStatus],
    cluster: Optional[str],
):
    """Show the status of managed spot jobs.

    Jobs are shown in submission time order.

    Examples:
        sky spot queue
        sky spot queue -a
        sky spot queue mymanagedjob
        sky spot queue mycluster --all  # Show jobs on a cluster.
        sky spot queue 1 2            # Show jobs with job IDs 1 and 2.
    """
    if skip_finished:
        click.secho(
            'WARNING: --skip-finished is deprecated and will be removed in a future'
            ' release. Use --all/-a instead.',
            fg='yellow',
        )
        all = not skip_finished
    job_ids: Optional[List[int]] = None
    job_names: Optional[List[str]] = None
    if job_names_or_ids:
        # Parse job_names_or_ids into job_ids and job_names
        job_ids = []
        job_names = []
        for job_name_or_id in job_names_or_ids:
            try:
                job_ids.append(int(job_name_or_id))
            except ValueError:
                job_names.append(job_name_or_id)
        if not job_ids:
            job_ids = None
        if not job_names:
            job_names = None

    if status is not None:
        status = job_lib.JobStatus(status)
    try:
        sky.spot_queue(job_ids=job_ids,
                       job_names=job_names,
                       cluster_name=cluster,
                       all=all,
                       statuses=status)
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky spot queue')
        sys.exit(1)


@spot.command('logs', cls=_DocumentedCodeCommand)
@click.argument('job_name_or_id',
                required=False,
                type=str,
                **_get_shell_complete_args(_complete_spot_job_names_or_ids))
@_job_status_option
@click.option(
    '--follow',
    '-f',
    is_flag=True,
    default=False,
    help='Stream logs live.',
)
@config_option(expose_value=False)
@usage_lib.entrypoint
@timeline.event
def spot_logs(job_name_or_id: Optional[str], status: Optional[job_lib.JobStatus],
                follow: bool):
    """Show logs of managed spot jobs.

    If job name or ID is not specified, show the logs of the latest job.
    If --status is specified, show the logs of all jobs with that status.

    Examples:
        sky spot logs
        sky spot logs mymanagedjob
        sky spot logs 1
        sky spot logs --status PENDING
    """
    job_id: Optional[int] = None
    job_name: Optional[str] = None
    if job_name_or_id is not None:
        try:
            job_id = int(job_name_or_id)
        except ValueError:
            job_name = job_name_or_id

    if status is not None:
        if job_id is not None or job_name is not None:
            with ux_utils.print_exception_no_traceback():
                raise click.UsageError(
                    'Cannot specify both job name/ID and --status.')
        status = job_lib.JobStatus(status)
        click.secho(f'Showing logs for all {status.value} jobs:', fg='yellow')
    try:
        sky.spot_logs(job_id=job_id,
                      job_name=job_name,
                      status_filter=status,
                      follow=follow)
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky spot logs')
        sys.exit(1)


@spot.command('cancel', cls=_DocumentedCodeCommand)
@click.argument('job_names_or_ids',
                required=False,
                type=str,
                nargs=-1,
                **_get_shell_complete_args(_complete_spot_job_names_or_ids))
@click.option(
    '--all',
    '-a',
    is_flag=True,
    default=False,
    help='Cancel all spot jobs.',
)
@click.option(
    '--yes',
    '-y',
    is_flag=True,
    default=False,
    help='Skip confirmation prompts.',
)
@config_option(expose_value=False)
@usage_lib.entrypoint
@timeline.event
def spot_cancel(job_names_or_ids: Tuple[str], all: bool, yes: bool):  # pylint: disable=redefined-outer-name
    """Cancel managed spot jobs.

    If job names or IDs are not specified, cancel the latest running spot job.

    Examples:
        sky spot cancel
        sky spot cancel -a
        sky spot cancel mymanagedjob
        sky spot cancel 1 2
    """
    job_ids: Optional[List[int]] = None
    job_names: Optional[List[str]] = None
    if job_names_or_ids:
        # Parse job_names_or_ids into job_ids and job_names
        job_ids = []
        job_names = []
        for job_name_or_id in job_names_or_ids:
            try:
                job_ids.append(int(job_name_or_id))
            except ValueError:
                job_names.append(job_name_or_id)
        if not job_ids:
            job_ids = None
        if not job_names:
            job_names = None
    elif not all:
        # Cancel the latest job if no job name/ID is specified.
        latest_job = spot_lib.get_latest_job()
        if latest_job is None:
            click.secho('No spot jobs found.', fg='yellow')
            return
        job_ids = [latest_job['job_id']]

    try:
        sky.spot_cancel(job_ids=job_ids,
                        job_names=job_names,
                        all=all,
                        no_confirm=yes)
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky spot cancel')
        sys.exit(1)


@spot.command('recover', cls=_DocumentedCodeCommand)
@click.argument('job_names_or_ids',
                required=True,
                type=str,
                nargs=-1,
                **_get_shell_complete_args(_complete_spot_job_names_or_ids))
@click.option(
    '--yes',
    '-y',
    is_flag=True,
    default=False,
    help='Skip confirmation prompts.',
)
@config_option(expose_value=False)
@usage_lib.entrypoint
@timeline.event
def spot_recover(job_names_or_ids: Tuple[str], yes: bool):
    """Force recover managed spot jobs in the RECOVERING state.

    Examples:
        sky spot recover 1
        sky spot recover mymanagedjob
    """
    job_ids: Optional[List[int]] = None
    job_names: Optional[List[str]] = None
    if job_names_or_ids:
        # Parse job_names_or_ids into job_ids and job_names
        job_ids = []
        job_names = []
        for job_name_or_id in job_names_or_ids:
            try:
                job_ids.append(int(job_name_or_id))
            except ValueError:
                job_names.append(job_name_or_id)
        if not job_ids:
            job_ids = None
        if not job_names:
            job_names = None
    else:
        # Should not happen, as job_names_or_ids is required.
        with ux_utils.print_exception_no_traceback():
            raise click.UsageError('Job name or ID must be specified.')
    try:
        sky.spot_recover(job_ids=job_ids, job_names=job_names, no_confirm=yes)
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky spot recover')
        sys.exit(1)


@spot.command('tail-logs', cls=_DocumentedCodeCommand)
@click.argument('job_name_or_id',
                required=False,
                type=str,
                **_get_shell_complete_args(_complete_spot_job_names_or_ids))
@_job_status_option
@usage_lib.entrypoint
@timeline.event
def spot_tail_logs(job_name_or_id: Optional[str],
                     status: Optional[job_lib.JobStatus]):
    """DEPRECATED. Use `sky spot logs -f` instead."""
    # TODO(zhwu): Remove this command after 2 minor releases.
    click.secho(
        'WARNING: `sky spot tail-logs` is deprecated and will be removed in a'
        ' future release. Use `sky spot logs -f` instead.',
        fg='yellow',
    )
    job_id: Optional[int] = None
    job_name: Optional[str] = None
    if job_name_or_id is not None:
        try:
            job_id = int(job_name_or_id)
        except ValueError:
            job_name = job_name_or_id

    if status is not None:
        if job_id is not None or job_name is not None:
            with ux_utils.print_exception_no_traceback():
                raise click.UsageError(
                    'Cannot specify both job name/ID and --status.')
        status = job_lib.JobStatus(status)
        click.secho(f'Showing logs for all {status.value} jobs:', fg='yellow')
    try:
        sky.spot_logs(job_id=job_id,
                      job_name=job_name,
                      status_filter=status,
                      follow=True)
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky spot tail-logs')
        sys.exit(1)


@cli.group(cls=_NaturalOrderGroup)
def serve():
    """SkyServe: Serve ML models and applications."""
    pass


@serve.command('up', cls=_DocumentedCodeCommand)
@click.argument(
    'yaml_config',
    required=False,
    type=str,
    **_get_shell_complete_args(_complete_file_name),  # type: ignore[arg-type]
)
@click.option(
    '--service-name',
    '-n',
    default=None,
    type=str,
    required=False,
    help='Assign a name to the service. If not specified, a name will be'
    ' generated.',
)
@click.option(
    '--cloud',
    type=str,
    required=False,
    help=(
        'The cloud provider to use. If not specified, will try to use the default'
        ' cloud configured in the SkyPilot config file.'),
)
@click.option(
    '--region',
    type=str,
    required=False,
    help=('The region to use. If not specified, will try to use the default'
          ' region configured for the cloud.'),
)
@click.option(
    '--zone',
    type=str,
    required=False,
    help=('The zone to use. If not specified, will try to use the default'
          ' zone configured for the cloud and region.'),
)
@click.option(
    '--cpus',
    type=str,
    default=None,
    help='Number of vCPUs requested (e.g., ''4'', ''4+''). Overrides the'
    ' ''cpus'' field in the YAML.',
)
@click.option(
    '--memory',
    type=str,
    default=None,
    help='Memory requested (e.g., ''16G'', ''16G+''). Overrides the'
    ' ''memory'' field in the YAML.',
)
@click.option(
    '--gpus',
    type=str,
    default=None,
    required=False,
    help='Type and number of GPUs. Overrides the ''accelerators'' field'
    ' in the YAML.',
)
@click.option(
    '--accelerators',
    type=str,
    default=None,
    required=False,
    help='Synonym for --gpus. Overrides the ''accelerators'' field in the YAML.',
)
@click.option(
    '--use-spot/--no-use-spot',
    default=None,
    help='Whether to request spot instances. Overrides the ''use_spot'' field'
    ' in the YAML.',
)
@click.option(
    '--spot-recovery',
    type=str,
    default=None,
    help='Spot recovery strategy to use. Overrides the ''spot_recovery'' field'
    ' in the YAML.',
)
@click.option(
    '--image-id',
    type=str,
    default=None,
    help='Custom image id to use. Overrides the ''image_id'' field in the YAML.',
)
@click.option(
    '--disk-size',
    type=int,
    default=None,
    help='OS disk size in GB. Overrides the ''disk_size'' field in the YAML.',
)
@click.option(
    '--disk-tier',
    type=click.Choice(['high', 'medium', 'low', 'best']),
    default=None,
    help='OS disk tier. Overrides the ''disk_tier'' field in the YAML.',
)
@click.option(
    '--env',
    required=False,
    type=str,
    multiple=True,
    help='Environment variables to set. It can be used multiple times. Example:'          ' --env MY_VAR=1 --env MY_VAR2=2.',
)
@click.option(
    '--workdir',
    default=None,
    type=str,
    required=False,
    help='If specified, sync this local directory to the remote cluster,
    ''and use it as the working directory. Overrides the ''workdir'' field
    ''in the YAML. If the specified path is relative, it is assumed to be
    ''relative to the directory containing the YAML.
    ''[default: None]'
)
@click.option(
    '--yes',
    '-y',
    is_flag=True,
    default=False,
    help='Skip confirmation prompts.',
)
@click.option(
    '--detach',
    '-d',
    is_flag=True,
    default=False,
    help='Detach from the service logs after launching.',
)
@click.option(
    '--retry-until-up',
    is_flag=True,
    default=False,
    required=False,
    help='Whether to retry launching the service cluster until it is up.')
@usage_lib.entrypoint
@timeline.event
def serve_up(
    yaml_config: Optional[str],
    service_name: Optional[str],
    cloud: Optional[str],
    region: Optional[str],
    zone: Optional[str],
    cpus: Optional[str],
    memory: Optional[str],
    gpus: Optional[str],
    accelerators: Optional[str],
    use_spot: Optional[bool],
    spot_recovery: Optional[str],
    image_id: Optional[str],
    disk_size: Optional[int],
    disk_tier: Optional[str],
    env: Tuple[str, ...],
    workdir: Optional[str],
    yes: bool,
    detach: bool,
    retry_until_up: bool,
):
    """Launch a service from a YAML file.

    Example:
        sky serve up service.yaml -n my-service
    """
    if accelerators is not None:
        if gpus is not None:
            with ux_utils.print_exception_no_traceback():
                raise click.UsageError(
                    'Cannot specify both --gpus and --accelerators.')
        gpus = accelerators
    envs = _parse_env_vars(env)
    click.echo('Launching a new service...')
    try:
        # This will validate the yaml file and fill in the defaults.
        spec = sky_serve.SkyServiceSpec.from_yaml(yaml_config)
        # Override the resources.
        _override_service_resources(
            spec,
            cloud=cloud,
            region=region,
            zone=zone,
            cpus=cpus,
            memory=memory,
            gpus=gpus,
            use_spot=use_spot,
            spot_recovery=spot_recovery,
            image_id=image_id,
            disk_size=disk_size,
            disk_tier=disk_tier,
        )
        # Override workdir.
        if workdir is not None:
            spec.workdir = workdir
        # Override envs.
        if envs:
            spec.set_envs(envs)

        env_vars = sky.get_environment_variables()
        if env_vars:
            # Propagate environment variables starting with SKYPILOT_.
            spec.update_envs(env_vars)
        sky_serve.up(
            spec=spec,
            service_name=service_name,
            retry_until_up=retry_until_up,
            no_confirm=yes,
            detach=detach,
        )
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky serve up')
        sys.exit(1)


@serve.command('down', cls=_DocumentedCodeCommand)
@click.argument('service_names',
                required=False,
                type=str,
                nargs=-1,
                **_get_shell_complete_args(_complete_service_names))
@click.option(
    '--all',
    '-a',
    is_flag=True,
    default=False,
    help='Terminate all services.',
)
@click.option(
    '--yes',
    '-y',
    is_flag=True,
    default=False,
    help='Skip confirmation prompts.',
)
@click.option(
    '--keep-replica-cluster/--no-keep-replica-cluster',
    default=False,
    required=False,
    help='Whether to keep the replica clusters running after terminating the'           ' service.',
)
@config_option(expose_value=False)
@usage_lib.entrypoint
@timeline.event
def serve_down(service_names: Tuple[str], all: bool, yes: bool,  # pylint: disable=redefined-outer-name
                 keep_replica_cluster: bool):
    """Terminate services.

    If service names are not specified, terminate all services.

    Examples:
        sky serve down my-service
        sky serve down service1 service2
        sky serve down -a
    """
    if all:
        if service_names:
            with ux_utils.print_exception_no_traceback():
                raise click.UsageError(
                    'Cannot specify service names when --all/-a is specified.')
        service_names = None
    elif not service_names:
        with ux_utils.print_exception_no_traceback():
            raise click.UsageError(
                'Either service names or --all/-a must be specified.')
    try:
        sky_serve.down(
            service_names=list(service_names) if service_names else None,
            keep_replica_cluster=keep_replica_cluster,
            no_confirm=yes)
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky serve down')
        sys.exit(1)


@serve.command('status', cls=_DocumentedCodeCommand)
@click.argument('service_names',
                required=False,
                type=str,
                nargs=-1,
                **_get_shell_complete_args(_complete_service_names))
@click.option(
    '--all',
    '-a',
    is_flag=True,
    default=False,
    help='Show all services (including terminated services).'
)
@click.option(
    '--endpoint',
    is_flag=True,
    default=False,
    help='Show the endpoint of the service.',
)
@click.option(
    '--skip-replica-info',
    is_flag=True,
    default=False,
    help='Do not show replica information.',
)
@click.option(
    '--refresh',
    is_flag=True,
    default=False,
    help='Fetch the latest status of the service(s) from the controller.')
@config_option(expose_value=False)
@usage_lib.entrypoint
@timeline.event
def serve_status(service_names: Tuple[str], all: bool, endpoint: bool,  # pylint: disable=redefined-outer-name
                 skip_replica_info: bool, refresh: bool):
    """Show the status of services.

    If service names are not specified, show the status of all services.

    Examples:
        sky serve status
        sky serve status my-service
        sky serve status service1 service2
        sky serve status --endpoint my-service
    """
    if not service_names:
        service_names = None
    if endpoint:
        if service_names is None or len(service_names) > 1:
            with ux_utils.print_exception_no_traceback():
                raise click.UsageError(
                    'Cannot show endpoint for multiple services.')
        if all:
            with ux_utils.print_exception_no_traceback():
                raise click.UsageError('Cannot show endpoint for all services.')
        if skip_replica_info:
            with ux_utils.print_exception_no_traceback():
                raise click.UsageError(
                    'Cannot skip replica info when showing endpoint.')
    try:
        sky_serve.status(
            service_names=list(service_names) if service_names else None,
            show_endpoint=endpoint,
            skip_replica_info=skip_replica_info,
            show_history=all,
            refresh=refresh)
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky serve status')
        sys.exit(1)


@serve.command('logs', cls=_DocumentedCodeCommand)
@click.argument('service_name',
                required=True,
                type=str,
                **_get_shell_complete_args(_complete_service_names))
@click.option('--replica-id', type=int, help='Replica ID to show logs for.')
@click.option(
    '--follow',
    '-f',
    is_flag=True,
    default=False,
    help='Stream logs live.',
)
@usage_lib.entrypoint
@timeline.event
def serve_logs(service_name: str, replica_id: Optional[int], follow: bool):
    """Show logs of services.

    If replica ID is not specified, show the logs of the load balancer.

    Examples:
        sky serve logs my-service
        sky serve logs my-service --replica-id 1
    """
    try:
        sky_serve.logs(service_name=service_name,
                       replica_id=replica_id,
                       follow=follow)
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky serve logs')
        sys.exit(1)


@serve.command('tail-logs', cls=_DocumentedCodeCommand)
@click.argument('service_name',
                required=True,
                type=str,
                **_get_shell_complete_args(_complete_service_names))
@click.option('--replica-id', type=int, help='Replica ID to show logs for.')
@usage_lib.entrypoint
@timeline.event
def serve_tail_logs(service_name: str, replica_id: Optional[int]):
    """DEPRECATED. Use `sky serve logs -f` instead."""
    # TODO(zhwu): Remove this command after 2 minor releases.
    click.secho(
        'WARNING: `sky serve tail-logs` is deprecated and will be removed in a'
        ' future release. Use `sky serve logs -f` instead.',
        fg='yellow',
    )
    try:
        sky_serve.logs(service_name=service_name,
                       replica_id=replica_id,
                       follow=True)
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky serve tail-logs')
        sys.exit(1)


def _show_config():
    """Show the config file."""
    config_path = sky.check_config()
    if config_path is None:
        click.secho('Config file not found.', fg='red')
        sys.exit(1)
    with open(config_path, 'r', encoding='utf-8') as f:
        print(f.read())


def _check_yaml_and_entrypoint(ctx: click.Context, yaml_config: Optional[str],
                               entrypoint: Tuple[str, ...]):
    """Check if yaml_config and entrypoint are valid."""
    if yaml_config is None and not entrypoint:
        click.echo(ctx.get_help())
        ctx.exit()
    if yaml_config is not None and entrypoint:
        with ux_utils.print_exception_no_traceback():
            raise click.UsageError(
                'Cannot specify both YAML config and entrypoint command.')
    if yaml_config is not None:
        # Support path to file or raw YAML string.
        if '\n' not in yaml_config:
            yaml_config = os.path.expanduser(yaml_config)
            if not os.path.exists(yaml_config):
                with ux_utils.print_exception_no_traceback():
                    raise click.UsageError(
                        f'YAML config {common_utils.format_sky_api_list([yaml_config])}'                          ' not found.')


@timeline.event
def _make_task(yaml_config: Optional[str],
                 entrypoint: Tuple[str, ...]) -> 'task_lib.Task':
    """Make a task from yaml_config or entrypoint."""
    if yaml_config is not None:
        return sky.Task.from_yaml(yaml_config)
    else:
        assert entrypoint, (yaml_config, entrypoint)
        if entrypoint[0] == '--':
            entrypoint = entrypoint[1:]
        # 'sky launch -- python --version'
        # entrypoint = ('python', '--version')
        task = sky.Task(run=' '.join(entrypoint))
        return task


@timeline.event
def _override_task_resources(task: 'task_lib.Task',
                             cloud: Optional[str] = None,
                             region: Optional[str] = None,
                             zone: Optional[str] = None,
                             num_nodes: Optional[int] = None,
                             gpus: Optional[str] = None,
                             cpus: Optional[str] = None,
                             memory: Optional[str] = None,
                             use_spot: Optional[bool] = None,
                             image_id: Optional[str] = None,
                             disk_size: Optional[int] = None,
                             disk_tier: Optional[str] = None,
                             spot_recovery: Optional[str] = None):
    resources = task.resources
    assert len(resources) == 1
    resource = list(resources)[0]
    if cloud is not None:
        resource = resource.copy(cloud=clouds.CLOUD_REGISTRY.from_str(cloud))
    if region is not None:
        resource = resource.copy(region=region)
    if zone is not None:
        resource = resource.copy(zone=zone)
    if gpus is not None:
        try:
            accelerators = resources_utils.parse_accelerators(gpus)
        except ValueError as e:
            with ux_utils.print_exception_no_traceback():
                raise click.UsageError(str(e))
        resource = resource.copy(accelerators=accelerators)
    if cpus is not None:
        resource = resource.copy(cpus=cpus)
    if memory is not None:
        resource = resource.copy(memory=memory)
    if use_spot is not None:
        resource = resource.copy(use_spot=use_spot)
    if image_id is not None:
        resource = resource.copy(image_id=image_id)
    if disk_size is not None:
        resource = resource.copy(disk_size=disk_size)
    if disk_tier is not None:
        resource = resource.copy(disk_tier=disk_tier)
    if spot_recovery is not None:
        resource = resource.copy(spot_recovery=spot_recovery)
    task.set_resources({resource})
    if num_nodes is not None:
        task.num_nodes = num_nodes


@timeline.event
def _override_service_resources(
        spec: 'sky_serve.SkyServiceSpec',
        cloud: Optional[str] = None,
        region: Optional[str] = None,
        zone: Optional[str] = None,
        cpus: Optional[str] = None,
        memory: Optional[str] = None,
        gpus: Optional[str] = None,
        use_spot: Optional[bool] = None,
        spot_recovery: Optional[str] = None,
        image_id: Optional[str] = None,
        disk_size: Optional[int] = None,
        disk_tier: Optional[str] = None):
    resource = spec.resources
    if cloud is not None:
        resource = resource.copy(cloud=clouds.CLOUD_REGISTRY.from_str(cloud))
    if region is not None:
        resource = resource.copy(region=region)
    if zone is not None:
        resource = resource.copy(zone=zone)
    if gpus is not None:
        try:
            accelerators = resources_utils.parse_accelerators(gpus)
        except ValueError as e:
            with ux_utils.print_exception_no_traceback():
                raise click.UsageError(str(e))
        resource = resource.copy(accelerators=accelerators)
    if cpus is not None:
        resource = resource.copy(cpus=cpus)
    if memory is not None:
        resource = resource.copy(memory=memory)
    if use_spot is not None:
        resource = resource.copy(use_spot=use_spot)
    if spot_recovery is not None:
        resource = resource.copy(spot_recovery=spot_recovery)
    if image_id is not None:
        resource = resource.copy(image_id=image_id)
    if disk_size is not None:
        resource = resource.copy(disk_size=disk_size)
    if disk_tier is not None:
        resource = resource.copy(disk_tier=disk_tier)
    spec.resources = resource


@cli.command()
@click.option('--shell',
              required=True,
              type=click.Choice(['bash', 'zsh', 'fish']),
              help='Shell type.')
@usage_lib.entrypoint
def completion(shell: str):
    """Install autocompletion script for SkyPilot CLI."""
    script: Optional[str] = None
    if shell == 'bash':
        script = _COMPLETION_SCRIPT_BASH
    elif shell == 'zsh':
        script = _COMPLETION_SCRIPT_ZSH
    elif shell == 'fish':
        script = _COMPLETION_SCRIPT_FISH
    else:
        # Should not happen as click.Choice should raise an error.
        with ux_utils.print_exception_no_traceback():
            raise click.UsageError(f'Unsupported shell type: {shell}')
    click.echo(script)


@cli.command()
@click.argument('query', required=True, type=str)
@usage_lib.entrypoint
def search(query: str):
    """Search for examples in the SkyPilot documentation."""
    click.launch(f'https://skypilot.readthedocs.io/en/latest/search.html?q={query}')


@cli.group(cls=_NaturalOrderGroup)
def admin():
    """Admin commands for managing SkyPilot controller & API server."""
    pass


@admin.command('deploy', cls=_DocumentedCodeCommand)
@click.argument('config_file',
                required=False,
                type=str,
                default=None,
                **_get_shell_complete_args(_complete_file_name))
@click.option(
    '--yes',
    '-y',
    is_flag=True,
    default=False,
    help='Skip confirmation prompts.',
)
@click.option(
    '--update',
    is_flag=True,
    default=False,
    help='Update the SkyPilot controller & API server to the latest version.',
)
@usage_lib.entrypoint
def admin_deploy(config_file: Optional[str], yes: bool, update: bool):
    """Deploy the SkyPilot controller & API server.

    Examples:
        sky admin deploy config.yaml
    """
    if update:
        if config_file is not None:
            with ux_utils.print_exception_no_traceback():
                raise click.UsageError(
                    'Cannot specify config file when updating the controller.')
        try:
            kubernetes_utils.update_remotely(no_confirm=yes)
        except Exception as e:  # pylint: disable=broad-except
            cli_utils.handle_exception(e, 'sky admin deploy --update')
            sys.exit(1)
        return

    if config_file is None:
        with ux_utils.print_exception_no_traceback():
            raise click.UsageError('Config file must be specified.')
    config_file = os.path.expanduser(config_file)
    if not os.path.exists(config_file):
        with ux_utils.print_exception_no_traceback():
            raise click.UsageError(f'Config file {config_file} not found.')
    try:
        kubernetes_utils.deploy_remotely(config_file, no_confirm=yes)
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky admin deploy')
        sys.exit(1)


@admin.command('logs', cls=_DocumentedCodeCommand)
@click.argument('component',
                required=False,
                type=click.Choice(['controller', 'apiserver']),
                default='controller')
@click.option(
    '--follow',
    '-f',
    is_flag=True,
    default=False,
    help='Stream logs live.',
)
@usage_lib.entrypoint
def admin_logs(component: str, follow: bool):
    """Show logs of the SkyPilot controller & API server.

    Examples:
        sky admin logs
        sky admin logs controller
        sky admin logs apiserver
        sky admin logs -f
    """
    try:
        kubernetes_utils.logs_remotely(component, follow)
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky admin logs')
        sys.exit(1)


@admin.command('down', cls=_DocumentedCodeCommand)
@click.option(
    '--yes',
    '-y',
    is_flag=True,
    default=False,
    help='Skip confirmation prompts.',
)
@usage_lib.entrypoint
def admin_down(yes: bool):
    """Tear down the SkyPilot controller & API server.

    Examples:
        sky admin down
    """
    try:
        kubernetes_utils.down_remotely(no_confirm=yes)
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky admin down')
        sys.exit(1)


@admin.command('generate-kubeconfig')
@usage_lib.entrypoint
def admin_generate_kubeconfig():
    """Generate kubeconfig file for the SkyPilot controller."""
    try:
        kubernetes_utils.generate_kubeconfig_remotely()
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky admin generate-kubeconfig')
        sys.exit(1)


@admin.group(cls=_NaturalOrderGroup)
def policy():
    """Manage Admin Policies."""
    pass


@policy.command('fetch', cls=_DocumentedCodeCommand)
@click.option(
    '--output',
    '-o',
    type=str,
    required=False,
    help='Output file path to save the policy. If not specified, print to stdout.',
    **_get_shell_complete_args(_complete_file_name),  # type: ignore[arg-type]
)
@usage_lib.entrypoint
def policy_fetch(output: Optional[str]):
    """Fetch policies from the SkyPilot controller."""
    try:
        admin_policy_utils.fetch_policy(output)
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky admin policy fetch')
        sys.exit(1)


@policy.command('set', cls=_DocumentedCodeCommand)
@click.argument('policy_file',
                required=True,
                type=str,
                **_get_shell_complete_args(_complete_file_name))
@click.option(
    '--yes',
    '-y',
    is_flag=True,
    default=False,
    help='Skip confirmation prompts.',
)
@usage_lib.entrypoint
def policy_set(policy_file: str, yes: bool):
    """Set policies for the SkyPilot controller."""
    policy_file = os.path.expanduser(policy_file)
    if not os.path.exists(policy_file):
        with ux_utils.print_exception_no_traceback():
            raise click.UsageError(f'Policy file {policy_file} not found.')
    try:
        admin_policy_utils.set_policy(policy_file, no_confirm=yes)
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky admin policy set')
        sys.exit(1)


@policy.command('delete', cls=_DocumentedCodeCommand)
@click.option(
    '--yes',
    '-y',
    is_flag=True,
    default=False,
    help='Skip confirmation prompts.',
)
@usage_lib.entrypoint
def policy_delete(yes: bool):
    """Delete policies from the SkyPilot controller."""
    try:
        admin_policy_utils.delete_policy(no_confirm=yes)
    except Exception as e:  # pylint: disable=broad-except
        cli_utils.handle_exception(e, 'sky admin policy delete')
        sys.exit(1)


# Helper functions for autocompletion.
def _get_shell_complete_args(
    completer: Callable[[
        click.Context,
        click.Parameter,
        str,
    ], List[Union[str, click.shell_completion.CompletionItem]]],
) -> Dict[str, Any]:
    return {
        'shell_complete': functools.partial(_check_completion_supported, completer)
    }


def _check_completion_supported(
    completer: Callable[[
        click.Context,
        click.Parameter,
        str,
    ], List[Union[str, click.shell_completion.CompletionItem]]],
    ctx: click.Context,
    param: click.Parameter,
    incomplete: str,
) -> List[Union[str, click.shell_completion.CompletionItem]]:
    """Check if shell completion is supported and call the completer."""
    # Check if this is a completion request.
    if '\t' not in os.environ.get('_SKY_COMPLETE', ''):
        # Check if the completion script is installed.
        if os.environ.get('_SKY_COMPLETE') not in (
                'bash_complete',
                'zsh_complete',
                'fish_complete',
        ):
            # If not installed, don't show any completions.
            return []
    try:
        return completer(ctx, param, incomplete)
    except Exception as e:
        # Hide the traceback to avoid cluttering the terminal.
        # Write to stderr so that it won't be interpreted as completions.
        # Need to redirect stderr to /dev/null in the completion script.
        # TODO(zhwu): Find a better way to handle this.
        # print(f'Error during completion: {e}', file=sys.stderr)
        del e
        return []


def _complete_cluster_name(
    ctx: click.Context,
    param: click.Parameter,
    incomplete: str,
) -> List[str]:
    """Return a list of cluster names."""
    del ctx, param  # Unused.
    cluster_names = global_user_state.get_cluster_names(status_filter=None)
    return [name for name in cluster_names if name.startswith(incomplete)]


def _complete_cluster_name_including_stopped(
    ctx: click.Context,
    param: click.Parameter,
    incomplete: str,
) -> List[str]:
    """Return a list of cluster names including stopped ones."""
    del ctx, param  # Unused.
    cluster_names = global_user_state.get_cluster_names(
        status_filter=list(status_lib.ClusterStatus))
    return [name for name in cluster_names if name.startswith(incomplete)]


def _complete_stopped_cluster_name(
    ctx: click.Context,
    param: click.Parameter,
    incomplete: str,
) -> List[str]:
    """Return a list of stopped cluster names."""
    del ctx, param  # Unused.
    cluster_names = global_user_state.get_cluster_names(
        status_filter=[status_lib.ClusterStatus.STOPPED])
    return [name for name in cluster_names if name.startswith(incomplete)]


def _complete_file_name(
    ctx: click.Context,
    param: click.Parameter,
    incomplete: str,
) -> List[click.shell_completion.CompletionItem]:
    """Return a list of file names."""
    del ctx, param  # Unused.
    # Based on click.types.Path._complete().
    # https://github.com/pallets/click/blob/main/src/click/types.py
    items: List[click.shell_completion.CompletionItem] = []
    current_dir = os.getcwd()
    incomplete_path = os.path.join(current_dir, incomplete)
    incomplete_dir = os.path.dirname(incomplete_path)
    if not os.path.isdir(incomplete_dir):
        return []

    for name in os.listdir(incomplete_dir):
        path = os.path.join(incomplete_dir, name)
        if name.startswith(os.path.basename(incomplete_path)):
            # If the path is a directory, mark it as a directory.
            # This will add a trailing slash in bash/zsh.
            if os.path.isdir(path):
                items.append(click.shell_completion.CompletionItem(name, type='dir'))
            else:
                items.append(
                    click.shell_completion.CompletionItem(name, type='file'))

    return items


def _complete_storage_name(
    ctx: click.Context,
    param: click.Parameter,
    incomplete: str,
) -> List[str]:
    """Return a list of storage object names."""
    del ctx, param  # Unused.
    storage_names = [s['name'] for s in storage_lib.ls(all=True, quiet=True)]
    return [name for name in storage_names if name.startswith(incomplete)]


def _complete_file_or_storage_path(
    ctx: click.Context,
    param: click.Parameter,
    incomplete: str,
) -> List[Union[str, click.shell_completion.CompletionItem]]:
    """Return a list of file names or storage paths."""
    del ctx, param  # Unused.
    if ':' in incomplete:
        # Storage path completion.
        storage_name, path_prefix = incomplete.split(':', 1)
        try:
            storage_obj = storage_lib_ref.Storage.from_name(storage_name)
        except ValueError:
            # Storage name not found.
            return []
        try:
            storage_prefix = os.path.dirname(path_prefix)
            if not storage_prefix:
                storage_prefix = ''
            file_prefix = os.path.basename(path_prefix)
            # List the directory.
            possible_paths = storage_obj.ls(storage_prefix)
            if not possible_paths:
                return []
            # Filter by prefix.
            filtered_paths = [p for p in possible_paths if p.startswith(file_prefix)]
            # Format the output.
            return [
                f'{storage_name}:{os.path.join(storage_prefix, p)}'
                for p in filtered_paths
            ]
        except Exception:
            # Handle any errors during listing, e.g., connection errors.
            return []
    else:
        # File name completion.
        return _complete_file_name(ctx, param, incomplete)


def _complete_file_or_cluster_path(
    ctx: click.Context,
    param: click.Parameter,
    incomplete: str,
) -> List[Union[str, click.shell_completion.CompletionItem]]:
    """Return a list of file names or cluster paths."""
    # TODO(zhwu): Implement cluster path completion.
    return _complete_file_name(ctx, param, incomplete)


def _complete_spot_job_names_or_ids(
    ctx: click.Context,
    param: click.Parameter,
    incomplete: str,
) -> List[str]:
    """Return a list of managed spot job names or IDs."""
    del ctx, param  # Unused.
    # Get all jobs from the database.
    jobs = spot_lib.get_spot_jobs(show_all=True)
    names = [job['name'] for job in jobs if job['name'] is not None]
    ids = [str(job['job_id']) for job in jobs]
    # Filter by prefix.
    return [name for name in names + ids if name.startswith(incomplete)]


def _complete_service_names(
    ctx: click.Context,
    param: click.Parameter,
    incomplete: str,
) -> List[str]:
    """Return a list of service names."""
    del ctx, param  # Unused.
    # Get all services from the database.
    services = sky_serve.status(show_history=True, refresh=False)
    names = [service['name'] for service in services]
    # Filter by prefix.
    return [name for name in names if name.startswith(incomplete)]


def _complete_cloud_name(ctx, param, incomplete):
    # Combine canonical names and aliases
    all_cloud_names = set(cloud_registry.CLOUD_REGISTRY.keys()) | set(cloud_registry.CLOUD_REGISTRY._aliases.keys())
    return [
        name for name in all_cloud_names
        if name.startswith(incomplete)
    ]


def _parse_env_vars(env: Tuple[str, ...]) -> Dict[str, str]:
    """Parse environment variables from a tuple of strings."""
    envs = {}
    for e in env:
        if '=' not in e:
            with ux_utils.print_exception_no_traceback():
                raise click.UsageError(f'Invalid environment variable: {e}')
        key, value = e.split('=', 1)
        envs[key] = value
    return envs


def _parse_labels(label: Tuple[str, ...]) -> Dict[str, str]:
    """Parse labels from a tuple of strings."""
    labels = {}
    for l in label:
        if '=' not in l:
            with ux_utils.print_exception_no_traceback():
                raise click.UsageError(f'Invalid label: {l}')
        key, value = l.split('=', 1)
        labels[key] = value
    return labels


def _warn_if_local_cluster_exists(cluster_name: Optional[str]):
    """Warn if a local cluster with the same name already exists."""
    if cluster_name is None:
        return
    # Check if the cluster name is for a local cluster.
    # This is to prevent users from accidentally launching a remote cluster
    # with the same name as a local cluster.
    if backend_utils.is_local_cluster(cluster_name):
        click.secho(
            f'WARNING: Cluster {cluster_name!r} is a local cluster.'
            ' Launching a new remote cluster with the same name will'
            ' overwrite the local cluster configuration.',
            fg='yellow',
        )


# Add aliases for commands.
cli.add_command(status, name='cluster-status')
cli.add_command(queue, name='job-queue')
cli.add_command(job_status, name='job-status')

if __name__ == '__main__':
    cli()
