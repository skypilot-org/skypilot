"""Sky Initialization: check cloud credentials and enable clouds."""

import click

from sky import clouds
from sky import global_user_state


def init():
    click.echo('Checking credentials to enable clouds for Sky.')

    enabled_clouds = []
    for cloud in clouds.ALL_CLOUDS:
        click.echo(f'  Checking {cloud}...', nl=False)
        ok, reason = cloud.check_credentials()
        click.echo('\r', nl=False)
        status_msg = 'enabled' if ok else 'disabled'
        status_color = 'green' if ok else 'red'
        click.echo(
            '  ' +
            click.style(f'{cloud}: {status_msg}', fg=status_color, bold=True) +
            ' ' * 10)
        if ok:
            enabled_clouds.append(str(cloud))
        else:
            click.echo(f'    Reason: {reason}')

    if len(enabled_clouds) == 0:
        click.echo(
            click.style(
                'No cloud is enabled. Sky will not be able to run any task. '
                'Please setup access to a cloud, and rerun `sky init`.',
                fg='red',
                bold=True))
        raise SystemExit()
    else:
        click.echo('\nSky will use only the enabled clouds to run tasks. '
                   'To change this, configure cloud credentials, '
                   'and rerun ' + click.style('sky init', bold=True) + '.')

    global_user_state.set_enabled_clouds(enabled_clouds)
