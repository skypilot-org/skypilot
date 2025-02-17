"""Credential checks: check cloud credentials and enable clouds."""
import os
import traceback
from types import ModuleType
from typing import Dict, Iterable, List, Optional, Tuple, Union

import click
import colorama

from sky import clouds as sky_clouds
from sky import exceptions
from sky import global_user_state
from sky import skypilot_config
from sky.adaptors import cloudflare
from sky.utils import registry
from sky.utils import rich_utils
from sky.utils import ux_utils

CHECK_MARK_EMOJI = '\U00002714'  # Heavy check mark unicode
PARTY_POPPER_EMOJI = '\U0001F389'  # Party popper unicode


def check(
    quiet: bool = False,
    verbose: bool = False,
    clouds: Optional[Iterable[str]] = None,
) -> List[str]:
    echo = (lambda *_args, **_kwargs: None
           ) if quiet else lambda *args, **kwargs: click.echo(
               *args, **kwargs, color=True)
    echo('Checking credentials to enable clouds for SkyPilot.')
    enabled_clouds = []
    disabled_clouds = []

    def check_one_cloud(
            cloud_tuple: Tuple[str, Union[sky_clouds.Cloud,
                                          ModuleType]]) -> None:
        cloud_repr, cloud = cloud_tuple
        with rich_utils.safe_status(f'Checking {cloud_repr}...'):
            try:
                ok, reason = cloud.check_credentials()
            except Exception:  # pylint: disable=broad-except
                # Catch all exceptions to prevent a single cloud from blocking
                # the check for other clouds.
                ok, reason = False, traceback.format_exc()
        status_msg = 'enabled' if ok else 'disabled'
        styles = {'fg': 'green', 'bold': False} if ok else {'dim': True}
        echo('  ' + click.style(f'{cloud_repr}: {status_msg}', **styles) +
             ' ' * 30)
        if ok:
            enabled_clouds.append(cloud_repr)
            if verbose and cloud is not cloudflare:
                activated_account = cloud.get_active_user_identity_str()
                if activated_account is not None:
                    echo(f'    Activated account: {activated_account}')
            if reason is not None:
                echo(f'    Hint: {reason}')
        else:
            disabled_clouds.append(cloud_repr)
            echo(f'    Reason: {reason}')

    def get_cloud_tuple(
            cloud_name: str) -> Tuple[str, Union[sky_clouds.Cloud, ModuleType]]:
        # Validates cloud_name and returns a tuple of the cloud's name and
        # the cloud object. Includes special handling for Cloudflare.
        if cloud_name.lower().startswith('cloudflare'):
            return cloudflare.SKY_CHECK_NAME, cloudflare
        else:
            cloud_obj = registry.CLOUD_REGISTRY.from_str(cloud_name)
            assert cloud_obj is not None, f'Cloud {cloud_name!r} not found'
            return repr(cloud_obj), cloud_obj

    def get_all_clouds():
        return tuple([repr(c) for c in registry.CLOUD_REGISTRY.values()] +
                     [cloudflare.SKY_CHECK_NAME])

    if clouds is not None:
        cloud_list = clouds
    else:
        cloud_list = get_all_clouds()
    clouds_to_check = [get_cloud_tuple(c) for c in cloud_list]

    # Use allowed_clouds from config if it exists, otherwise check all clouds.
    # Also validate names with get_cloud_tuple.
    config_allowed_cloud_names = [
        get_cloud_tuple(c)[0] for c in skypilot_config.get_nested((
            'allowed_clouds',), get_all_clouds())
    ]
    # Use disallowed_cloud_names for logging the clouds that will be disabled
    # because they are not included in allowed_clouds in config.yaml.
    disallowed_cloud_names = [
        c for c in get_all_clouds() if c not in config_allowed_cloud_names
    ]
    # Check only the clouds which are allowed in the config.
    clouds_to_check = [
        c for c in clouds_to_check if c[0] in config_allowed_cloud_names
    ]

    for cloud_tuple in sorted(clouds_to_check):
        check_one_cloud(cloud_tuple)

    # Cloudflare is not a real cloud in registry.CLOUD_REGISTRY, and should
    # not be inserted into the DB (otherwise `sky launch` and other code would
    # error out when it's trying to look it up in the registry).
    enabled_clouds_set = {
        cloud for cloud in enabled_clouds if not cloud.startswith('Cloudflare')
    }
    disabled_clouds_set = {
        cloud for cloud in disabled_clouds if not cloud.startswith('Cloudflare')
    }
    config_allowed_clouds_set = {
        cloud for cloud in config_allowed_cloud_names
        if not cloud.startswith('Cloudflare')
    }
    previously_enabled_clouds_set = {
        repr(cloud) for cloud in global_user_state.get_cached_enabled_clouds()
    }

    # Determine the set of enabled clouds: (previously enabled clouds + newly
    # enabled clouds - newly disabled clouds) intersected with
    # config_allowed_clouds, if specified in config.yaml.
    # This means that if a cloud is already enabled and is not included in
    # allowed_clouds in config.yaml, it will be disabled.
    all_enabled_clouds = (config_allowed_clouds_set & (
        (previously_enabled_clouds_set | enabled_clouds_set) -
        disabled_clouds_set))
    global_user_state.set_enabled_clouds(list(all_enabled_clouds))

    disallowed_clouds_hint = None
    if disallowed_cloud_names:
        disallowed_clouds_hint = (
            '\nNote: The following clouds were disabled because they were not '
            'included in allowed_clouds in ~/.sky/config.yaml: '
            f'{", ".join([c for c in disallowed_cloud_names])}')
    if not all_enabled_clouds:
        echo(
            click.style(
                'No cloud is enabled. SkyPilot will not be able to run any '
                'task. Run `sky check` for more info.',
                fg='red',
                bold=True))
        if disallowed_clouds_hint:
            echo(click.style(disallowed_clouds_hint, dim=True))
        raise SystemExit()
    else:
        clouds_arg = (' ' +
                      ' '.join(disabled_clouds) if clouds is not None else '')
        echo(
            click.style(
                '\nTo enable a cloud, follow the hints above and rerun: ',
                dim=True) + click.style(f'sky check{clouds_arg}', bold=True) +
            '\n' + click.style(
                'If any problems remain, refer to detailed docs at: '
                'https://docs.skypilot.co/en/latest/getting-started/installation.html',  # pylint: disable=line-too-long
                dim=True))

        if disallowed_clouds_hint:
            echo(click.style(disallowed_clouds_hint, dim=True))

        # Pretty print for UX.
        if not quiet:
            enabled_clouds_str = '\n  ' + '\n  '.join([
                _format_enabled_cloud(cloud)
                for cloud in sorted(all_enabled_clouds)
            ])
            echo(f'\n{colorama.Fore.GREEN}{PARTY_POPPER_EMOJI} '
                 f'Enabled clouds {PARTY_POPPER_EMOJI}'
                 f'{colorama.Style.RESET_ALL}{enabled_clouds_str}')
    return enabled_clouds


def get_cached_enabled_clouds_or_refresh(
        raise_if_no_cloud_access: bool = False) -> List[sky_clouds.Cloud]:
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
        excluded_clouds: Optional[Iterable[sky_clouds.Cloud]]
) -> Dict[str, str]:
    """Returns the files necessary to access all clouds.

    Returns a dictionary that will be added to a task's file mounts
    and a list of patterns that will be excluded (used as rsync_exclude).
    """
    # Uploading credentials for all clouds instead of only sky check
    # enabled clouds because users may have partial credentials for some
    # clouds to access their specific resources (e.g. cloud storage) but
    # not have the complete credentials to pass sky check.
    clouds = registry.CLOUD_REGISTRY.values()
    file_mounts = {}
    for cloud in clouds:
        if (excluded_clouds is not None and
                sky_clouds.cloud_in_iterable(cloud, excluded_clouds)):
            continue
        cloud_file_mounts = cloud.get_credential_file_mounts()
        for remote_path, local_path in cloud_file_mounts.items():
            if os.path.exists(os.path.expanduser(local_path)):
                file_mounts[remote_path] = local_path
    # Currently, get_cached_enabled_clouds_or_refresh() does not support r2 as
    # only clouds with computing instances are marked as enabled by skypilot.
    # This will be removed when cloudflare/r2 is added as a 'cloud'.
    r2_is_enabled, _ = cloudflare.check_credentials()
    if r2_is_enabled:
        r2_credential_mounts = cloudflare.get_credential_file_mounts()
        file_mounts.update(r2_credential_mounts)
    return file_mounts


def _format_enabled_cloud(cloud_name: str) -> str:

    def _green_color(cloud_name: str) -> str:
        return f'{colorama.Fore.GREEN}{cloud_name}{colorama.Style.RESET_ALL}'

    if cloud_name == repr(sky_clouds.Kubernetes()):
        # Get enabled contexts for Kubernetes
        existing_contexts = sky_clouds.Kubernetes.existing_allowed_contexts()
        if not existing_contexts:
            return _green_color(cloud_name)

        # Check if allowed_contexts is explicitly set in config
        allowed_contexts = skypilot_config.get_nested(
            ('kubernetes', 'allowed_contexts'), None)

        # Format the context info with consistent styling
        if allowed_contexts is not None:
            contexts_formatted = []
            for i, context in enumerate(existing_contexts):
                symbol = (ux_utils.INDENT_LAST_SYMBOL
                          if i == len(existing_contexts) -
                          1 else ux_utils.INDENT_SYMBOL)
                contexts_formatted.append(f'\n    {symbol}{context}')
            context_info = f'Allowed contexts:{"".join(contexts_formatted)}'
        else:
            context_info = f'Active context: {existing_contexts[0]}'

        return (f'{_green_color(cloud_name)}\n'
                f'  {colorama.Style.DIM}{context_info}'
                f'{colorama.Style.RESET_ALL}')
    return _green_color(cloud_name)
