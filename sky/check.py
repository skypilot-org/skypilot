"""Credential checks: check cloud credentials and enable clouds."""
import traceback
from typing import Dict, Iterable, List, Optional, Tuple

import click
import colorama
import rich

from sky import clouds
from sky import exceptions
from sky import global_user_state
from sky.adaptors import cloudflare
from sky.utils import ux_utils


# TODO(zhwu): add check for a single cloud to improve performance
def check(quiet: bool = False, verbose: bool = False) -> None:
    echo = (lambda *_args, **_kwargs: None) if quiet else click.echo
    echo('Checking credentials to enable clouds for SkyPilot.')

    enabled_clouds = []

    def check_one_cloud(cloud_tuple: Tuple[str, clouds.Cloud]) -> None:
        cloud_repr, cloud = cloud_tuple
        echo(f'  Checking {cloud_repr}...', nl=False)
        try:
            ok, reason = cloud.check_credentials()
        except Exception:  # pylint: disable=broad-except
            # Catch all exceptions to prevent a single cloud from blocking the
            # check for other clouds.
            ok, reason = False, traceback.format_exc()
        echo('\r', nl=False)
        status_msg = 'enabled' if ok else 'disabled'
        styles = {'fg': 'green', 'bold': False} if ok else {'dim': True}
        echo('  ' + click.style(f'{cloud_repr}: {status_msg}', **styles) +
             ' ' * 30)
        if ok:
            enabled_clouds.append(cloud_repr)
            if verbose and cloud is not cloudflare:
                activated_account = cloud.get_current_user_identity_str()
                if activated_account is not None:
                    echo(f'    Activated account: {activated_account}')
            if reason is not None:
                echo(f'    Hint: {reason}')
        else:
            echo(f'    Reason: {reason}')

    clouds_to_check = [
        (repr(cloud), cloud) for cloud in clouds.CLOUD_REGISTRY.values()
    ]
    clouds_to_check.append(('Cloudflare, for R2 object store', cloudflare))

    for cloud_tuple in sorted(clouds_to_check):
        check_one_cloud(cloud_tuple)

    # Cloudflare is not a real cloud in clouds.CLOUD_REGISTRY, and should not be
    # inserted into the DB (otherwise `sky launch` and other code would error
    # out when it's trying to look it up in the registry).
    enabled_clouds = [
        cloud for cloud in enabled_clouds if not cloud.startswith('Cloudflare')
    ]
    global_user_state.set_enabled_clouds(enabled_clouds)

    if len(enabled_clouds) == 0:
        echo(
            click.style(
                'No cloud is enabled. SkyPilot will not be able to run any '
                'task. Run `sky check` for more info.',
                fg='red',
                bold=True))
        raise SystemExit()
    else:
        echo(
            click.style(
                '\nTo enable a cloud, follow the hints above and rerun: ',
                dim=True) + click.style('sky check', bold=True) + '\n' +
            click.style(
                'If any problems remain, refer to detailed docs at: '
                'https://skypilot.readthedocs.io/en/latest/getting-started/installation.html',  # pylint: disable=line-too-long
                dim=True))

        # Pretty print for UX.
        if not quiet:
            enabled_clouds_str = '\n  :heavy_check_mark: '.join(
                [''] + sorted(enabled_clouds))
            rich.print('\n[green]:tada: Enabled clouds :tada:'
                       f'{enabled_clouds_str}[/green]')


def get_cached_enabled_clouds_or_refresh(
        raise_if_no_cloud_access: bool = False) -> List[clouds.Cloud]:
    """Returns cached enabled clouds and if no cloud is enabled, refresh.

    This function will perform a refresh if no public cloud is enabled.

    Args:
        raise_if_no_cloud_access: if True, raise an exception if no public
            cloud is enabled.

    Raises:
        exceptions.NoCloudAccessError: if no public cloud is enabled and
            raise_if_no_cloud_access is set to True.
    """
    cached_enabled_clouds = global_user_state.get_cached_enabled_clouds()
    if not cached_enabled_clouds:
        try:
            check(quiet=True)
        except SystemExit:
            # If no cloud is enabled, check() will raise SystemExit.
            # Here we catch it and raise the exception later only if
            # raise_if_no_cloud_access is set to True.
            pass
        cached_enabled_clouds = global_user_state.get_cached_enabled_clouds()
    if raise_if_no_cloud_access and not cached_enabled_clouds:
        with ux_utils.print_exception_no_traceback():
            raise exceptions.NoCloudAccessError(
                'Cloud access is not set up. Run: '
                f'{colorama.Style.BRIGHT}sky check{colorama.Style.RESET_ALL}')
    return cached_enabled_clouds


def get_cloud_credential_file_mounts(
        excluded_clouds: Optional[Iterable[clouds.Cloud]]) -> Dict[str, str]:
    """Returns the files necessary to access all enabled clouds.

    Returns a dictionary that will be added to a task's file mounts
    and a list of patterns that will be excluded (used as rsync_exclude).
    """
    enabled_clouds = get_cached_enabled_clouds_or_refresh()
    file_mounts = {}
    for cloud in enabled_clouds:
        if (excluded_clouds is not None and
                clouds.cloud_in_list(cloud, excluded_clouds)):
            continue
        cloud_file_mounts = cloud.get_credential_file_mounts()
        file_mounts.update(cloud_file_mounts)
    # Currently, get_cached_enabled_clouds_or_refresh() does not support r2 as
    # only clouds with computing instances are marked as enabled by skypilot.
    # This will be removed when cloudflare/r2 is added as a 'cloud'.
    r2_is_enabled, _ = cloudflare.check_credentials()
    if r2_is_enabled:
        r2_credential_mounts = cloudflare.get_credential_file_mounts()
        file_mounts.update(r2_credential_mounts)
    return file_mounts
