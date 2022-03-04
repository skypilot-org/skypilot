"""Sky credential check: check cloud credentials and enable clouds."""
from typing import Dict, List, Tuple

import click

from sky import clouds
from sky import global_user_state


def check(quiet: bool = False) -> None:
    echo = (lambda *_args, **_kwargs: None) if quiet else click.echo
    echo('Checking credentials to enable clouds for Sky.')

    enabled_clouds = []
    for cloud in clouds.CLOUD_REGISTRY.values():
        echo(f'  Checking {cloud}...', nl=False)
        ok, reason = cloud.check_credentials()
        echo('\r', nl=False)
        status_msg = 'enabled' if ok else 'disabled'
        status_color = 'green' if ok else 'red'
        echo('  ' +
             click.style(f'{cloud}: {status_msg}', fg=status_color, bold=True) +
             ' ' * 10)
        if ok:
            enabled_clouds.append(str(cloud))
        else:
            echo(f'    Reason: {reason}')

    if len(enabled_clouds) == 0:
        click.echo(
            click.style(
                'No cloud is enabled. Sky will not be able to run any task. '
                'Run `sky check` for more info.',
                fg='red',
                bold=True))
        raise SystemExit()
    else:
        echo('\nSky will use only the enabled clouds to run tasks. '
             'To change this, configure cloud credentials, '
             'and run ' + click.style('sky check', bold=True) + '.')

    global_user_state.set_enabled_clouds(enabled_clouds)


def get_cloud_credential_file_mounts() -> Tuple[Dict[str, str], List[str]]:
    """Returns the files necessary to access all enabled clouds.

    Returns a dictionary that will be added to a task's file mounts 
    and a list of patterns that will be excluded (used as rsync_exclude).
    """
    enabled_clouds = global_user_state.get_enabled_clouds()
    file_mounts, excludes = {}, []
    for cloud in enabled_clouds:
        cloud_file_mounts, cloud_exclude = cloud.get_credential_file_mounts()
        file_mounts.update(cloud_file_mounts)
        excludes.extend(cloud_exclude)
    return file_mounts, excludes
