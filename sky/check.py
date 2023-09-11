"""Credential checks: check cloud credentials and enable clouds."""
from typing import Dict

import click

from sky import clouds
from sky import global_user_state
from sky.adaptors import cloudflare


# TODO(zhwu): add check for a single cloud to improve performance
def check(quiet: bool = False, verbose: bool = False) -> None:
    echo = (lambda *_args, **_kwargs: None) if quiet else click.echo
    echo('Checking credentials to enable clouds for SkyPilot.')

    enabled_clouds = []
    for cloud in clouds.CLOUD_REGISTRY.values():
        if not isinstance(cloud, clouds.Local):
            echo(f'  Checking {cloud}...', nl=False)
        ok, reason = cloud.check_credentials()
        echo('\r', nl=False)
        status_msg = 'enabled' if ok else 'disabled'
        status_color = 'green' if ok else 'red'
        if not isinstance(cloud, clouds.Local):
            echo('  ' + click.style(
                f'{cloud}: {status_msg}', fg=status_color, bold=True) +
                 ' ' * 30)
        if ok:
            enabled_clouds.append(str(cloud))
            if verbose:
                activated_account = cloud.get_current_user_identity_str()
                if activated_account is not None:
                    echo(f'    Activated account: {activated_account}')
            if reason is not None:
                echo(f'    Hint: {reason}')
        else:
            echo(f'    Reason: {reason}')

    # Currently, clouds.CLOUD_REGISTRY.values() does not
    # support r2 as only clouds with computing instances
    # are added as 'cloud'. This will be removed when
    # cloudflare/r2 is added as a 'cloud'.
    cloud = 'Cloudflare (for R2 object store)'
    echo(f'  Checking {cloud}...', nl=False)
    r2_is_enabled, reason = cloudflare.check_credentials()
    echo('\r', nl=False)
    status_msg = 'enabled' if r2_is_enabled else 'disabled'
    status_color = 'green' if r2_is_enabled else 'red'
    echo('  ' +
         click.style(f'{cloud}: {status_msg}', fg=status_color, bold=True) +
         ' ' * 30)
    if not r2_is_enabled:
        echo(f'    Reason: {reason}')

    if len(enabled_clouds) == 0 and not r2_is_enabled:
        click.echo(
            click.style(
                'No cloud is enabled. SkyPilot will not be able to run any '
                'task. Run `sky check` for more info.',
                fg='red',
                bold=True))
        raise SystemExit()
    else:
        echo('\nSkyPilot will use only the enabled clouds to run tasks. '
             'To change this, configure cloud credentials, '
             'and run ' + click.style('sky check', bold=True) + '.'
             '\n' + click.style(
                 'If any problems remain, please file an issue at '
                 'https://github.com/skypilot-org/skypilot/issues/new',
                 dim=True))

    global_user_state.set_enabled_clouds(enabled_clouds)


def get_cloud_credential_file_mounts() -> Dict[str, str]:
    """Returns the files necessary to access all enabled clouds.

    Returns a dictionary that will be added to a task's file mounts
    and a list of patterns that will be excluded (used as rsync_exclude).
    """
    enabled_clouds = global_user_state.get_enabled_clouds()
    file_mounts = {}
    for cloud in enabled_clouds:
        cloud_file_mounts = cloud.get_credential_file_mounts()
        file_mounts.update(cloud_file_mounts)
    # Currently, get_enabled_clouds() does not support r2
    # as only clouds with computing instances are marked
    # as enabled by skypilot. This will be removed when
    # cloudflare/r2 is added as a 'cloud'.
    r2_is_enabled, _ = cloudflare.check_credentials()
    if r2_is_enabled:
        r2_credential_mounts = cloudflare.get_credential_file_mounts()
        file_mounts.update(r2_credential_mounts)
    return file_mounts
