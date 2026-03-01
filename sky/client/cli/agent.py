"""CLI commands for agent sessions (powered by DevSpaces)."""
import subprocess
import time
from typing import Optional

import click
import colorama
import requests as requests_lib

from sky import sky_logging
from sky.server import common as server_common
from sky.usage import usage_lib

logger = sky_logging.init_logger(__name__)

_DEVSPACES_API_PREFIX = '/plugins/api/devspaces'

# How long to wait between status polls during launch
_POLL_INTERVAL_SECS = 5
_LAUNCH_TIMEOUT_SECS = 600  # 10 minutes


def _api_url(path: str) -> str:
    """Build full API URL for a DevSpaces endpoint."""
    return f'{server_common.get_server_url()}{_DEVSPACES_API_PREFIX}{path}'


def _api_get(path: str):
    """Make a GET request to the DevSpaces API."""
    url = _api_url(path)
    resp = requests_lib.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _api_post(path: str, json_data: Optional[dict] = None):
    """Make a POST request to the DevSpaces API."""
    url = _api_url(path)
    resp = requests_lib.post(url, json=json_data or {}, timeout=60)
    if not resp.ok:
        detail = resp.json().get('detail', resp.text)
        raise click.ClickException(detail)
    return resp.json()


def _api_delete(path: str):
    """Make a DELETE request to the DevSpaces API."""
    url = _api_url(path)
    resp = requests_lib.delete(url, timeout=60)
    if not resp.ok:
        detail = resp.json().get('detail', resp.text)
        raise click.ClickException(detail)
    return resp.json()


def _wait_for_running(name: str, timeout: int = _LAUNCH_TIMEOUT_SECS):
    """Poll until a devspace reaches RUNNING state."""
    start = time.time()
    while time.time() - start < timeout:
        status = _api_get(f'/status/{name}')
        state = status.get('state', '')
        if state == 'RUNNING':
            return status
        if state == 'ERROR':
            raise click.ClickException(
                f'Devspace {name!r} entered ERROR state during launch.')
        click.echo(f'  State: {state}... waiting')
        time.sleep(_POLL_INTERVAL_SECS)
    raise click.ClickException(
        f'Timed out waiting for devspace {name!r} to reach RUNNING state.')


def _attach_to_session(cluster_name: str):
    """SSH into the cluster and attach to the agent tmux session."""
    click.echo(f'{colorama.Style.DIM}'
               '─────────────────────────────────────────\n'
               f'{colorama.Style.RESET_ALL}')
    # Use ssh + tmux to attach to the agent session
    # tmux new-session -A -s agent reattaches if existing, creates if not
    cmd = [
        'ssh',
        '-t',
        cluster_name,
        '--',
        'tmux',
        'new-session',
        '-A',
        '-s',
        'agent',
        '-c',
        '/home/sky/devspace',
    ]
    try:
        proc = subprocess.run(cmd, check=False)
        return proc.returncode
    except KeyboardInterrupt:
        click.echo('\nDetached from agent session.')
        return 0


def _print_devspace_table(devspaces):
    """Print a formatted table of devspaces."""
    if not devspaces:
        click.echo('No agent sessions found.')
        return

    # Header
    header = (f'{"NAME":<20} {"STATE":<12} {"ENVIRONMENT":<15} '
              f'{"CLUSTER":<25} {"VOLUME":<10}')
    click.echo(header)
    click.echo('─' * len(header))

    for ds in devspaces:
        name = ds.get('name', '')
        state = ds.get('state', '')
        env_type = ds.get('environment_type', '')
        cluster = ds.get('cluster_name', '') or '-'
        volume = ds.get('volume_size', '')

        # Color the state
        if state == 'RUNNING':
            state_str = (f'{colorama.Fore.GREEN}{state}'
                         f'{colorama.Style.RESET_ALL}')
        elif state == 'ERROR':
            state_str = (f'{colorama.Fore.RED}{state}'
                         f'{colorama.Style.RESET_ALL}')
        elif state == 'LAUNCHING':
            state_str = (f'{colorama.Fore.CYAN}{state}'
                         f'{colorama.Style.RESET_ALL}')
        else:
            state_str = state

        # Pad with invisible chars accounting for color codes
        state_pad = 12 + len(state_str) - len(state)
        click.echo(f'{name:<20} {state_str:<{state_pad}} {env_type:<15} '
                   f'{cluster:<25} {volume:<10}')


def register_agent_commands(cli):
    """Register the agent command group with the CLI."""

    @cli.group(cls=click.Group)
    def agent():
        """Coding Agent Sessions (powered by DevSpaces)."""
        pass

    @agent.command('launch')
    @click.option('-c',
                  '--cluster',
                  'name',
                  required=True,
                  type=str,
                  help='Session name.')
    @click.option('--gpus',
                  default=None,
                  type=str,
                  help='GPU type and count (e.g., A100:1).')
    @click.option('--cpus',
                  default=None,
                  type=str,
                  help='CPU requirement (e.g., 4+).')
    @click.option('--memory',
                  default=None,
                  type=str,
                  help='Memory requirement (e.g., 8+).')
    @click.option('--cloud', default=None, type=str, help='Cloud provider.')
    @click.option('--idle-minutes',
                  default=30,
                  type=int,
                  help='Autostop idle timeout in minutes.')
    @click.option('--detach',
                  is_flag=True,
                  default=False,
                  help='Launch without attaching to the session.')
    @usage_lib.entrypoint
    def agent_launch(name, gpus, cpus, memory, cloud, idle_minutes, detach):
        """Launch or resume a coding agent session.

        Creates a DevSpace with Claude Code, provisions compute, and
        attaches to the session via SSH + tmux.

        If the devspace already exists in CREATED state, launches new
        compute. If already RUNNING, attaches directly.

        \b
        Examples:
          # Create and launch a new agent session
          sky agent launch -c dev

          # Launch with GPU
          sky agent launch -c gpu-work --gpus A100:1

          # Resume a paused session with different compute
          sky agent launch -c dev --gpus A100:1
        """
        # Step 1: Create devspace if it doesn't exist
        try:
            status = _api_get(f'/status/{name}')
        except requests_lib.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                status = None
            else:
                raise

        if status is None:
            click.echo(f'Creating devspace {name!r} with Claude Code...')
            _api_post(
                '/create', {
                    'name': name,
                    'environment_type': 'claude-code',
                    'volume_size': '50Gi',
                })
            status = _api_get(f'/status/{name}')

        state = status.get('state', '')
        cluster_name = status.get('cluster_name', '')

        # Step 2: Launch if not running
        if state != 'RUNNING':
            click.echo('Launching compute...')
            _api_post(
                '/launch', {
                    'name': name,
                    'cpus': cpus,
                    'memory': memory,
                    'gpus': gpus,
                    'cloud': cloud,
                    'idle_minutes': idle_minutes,
                })
            status = _wait_for_running(name)
            cluster_name = status.get('cluster_name', '')
            click.echo(
                f'{colorama.Fore.GREEN}Compute ready.'
                f'{colorama.Style.RESET_ALL}')
        else:
            click.echo(f'Devspace {name!r} is already running '
                       f'(cluster: {cluster_name}).')

        # Step 3: Attach if not detached
        if detach:
            click.echo(f'Devspace {name!r} launched. '
                       f'Attach with: sky agent attach {name}')
        else:
            if not cluster_name:
                raise click.ClickException(
                    'No cluster name found. Cannot attach.')
            click.echo(f'Attaching to session on {cluster_name}...')
            _attach_to_session(cluster_name)

    @agent.command('attach')
    @click.argument('name')
    @usage_lib.entrypoint
    def agent_attach(name):
        """Attach to a running agent session.

        Connects via SSH and attaches to the tmux session running
        Claude Code. Use Ctrl+B, D to detach.

        \b
        Example:
          sky agent attach dev
        """
        status = _api_get(f'/status/{name}')
        state = status.get('state', '')
        cluster_name = status.get('cluster_name', '')

        if state != 'RUNNING':
            raise click.ClickException(
                f'Devspace {name!r} is not running (state: {state}). '
                f'Launch with: sky agent launch -c {name}')

        if not cluster_name:
            raise click.ClickException('No cluster name found.')

        click.echo(f'Attaching to {name!r} (cluster: {cluster_name})...')
        _attach_to_session(cluster_name)

    @agent.command('list')
    @usage_lib.entrypoint
    def agent_list():
        """List agent sessions.

        Shows all devspaces with the claude-code environment type.
        """
        result = _api_get('/list?environment_type=claude-code')
        devspaces = result.get('devspaces', [])
        _print_devspace_table(devspaces)

    @agent.command('stop')
    @click.argument('name')
    @click.option('--yes',
                  '-y',
                  is_flag=True,
                  default=False,
                  help='Skip confirmation.')
    @usage_lib.entrypoint
    def agent_stop(name, yes):
        """Pause an agent session (volume preserved).

        Stops the cluster but keeps the persistent volume, so work
        is preserved. Resume with 'sky agent launch'.

        \b
        Example:
          sky agent stop dev
        """
        if not yes:
            click.confirm(
                f'Stop agent session {name!r}? '
                'The volume will be preserved.',
                abort=True)

        click.echo(f'Stopping {name!r}...')
        _api_post(f'/pause/{name}')
        click.echo(f'{colorama.Fore.GREEN}Stopped.{colorama.Style.RESET_ALL} '
                   f'Resume with: sky agent launch -c {name}')

    @agent.command('down')
    @click.argument('name')
    @click.option('--yes',
                  '-y',
                  is_flag=True,
                  default=False,
                  help='Skip confirmation.')
    @usage_lib.entrypoint
    def agent_down(name, yes):
        """Delete an agent session.

        Tears down the cluster and deletes the persistent volume.
        This cannot be undone.

        \b
        Example:
          sky agent down dev
        """
        if not yes:
            click.confirm(
                f'Delete agent session {name!r}? '
                'This will remove the cluster AND the volume.',
                abort=True)

        click.echo(f'Deleting {name!r}...')
        _api_delete(f'/{name}')
        click.echo(
            f'{colorama.Fore.GREEN}Deleted.{colorama.Style.RESET_ALL}')

    return agent
