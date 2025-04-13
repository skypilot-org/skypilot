"""Credential checks: check cloud credentials and enable clouds."""
import os
import traceback
from types import ModuleType
from typing import (Any, Callable, Dict, Iterable, List, Optional, Set, Tuple,
                    Union)

import click
import colorama

from sky import clouds as sky_clouds
from sky import exceptions
from sky import global_user_state
from sky import skypilot_config
from sky.adaptors import cloudflare
from sky.clouds import cloud as sky_cloud
from sky.utils import registry
from sky.utils import rich_utils
from sky.utils import ux_utils

CHECK_MARK_EMOJI = '\U00002714'  # Heavy check mark unicode
PARTY_POPPER_EMOJI = '\U0001F389'  # Party popper unicode


def check_capabilities(
    quiet: bool = False,
    verbose: bool = False,
    clouds: Optional[Iterable[str]] = None,
    capabilities: Optional[List[sky_cloud.CloudCapability]] = None,
) -> Dict[str, List[sky_cloud.CloudCapability]]:
    echo = (lambda *_args, **_kwargs: None
           ) if quiet else lambda *args, **kwargs: click.echo(
               *args, **kwargs, color=True)
    echo('Checking credentials to enable clouds for SkyPilot.')
    if capabilities is None:
        capabilities = sky_cloud.ALL_CAPABILITIES
    assert capabilities is not None
    enabled_clouds: Dict[str, List[sky_cloud.CloudCapability]] = {}
    disabled_clouds: Dict[str, List[sky_cloud.CloudCapability]] = {}

    def check_one_cloud(
            cloud_tuple: Tuple[str, Union[sky_clouds.Cloud,
                                          ModuleType]]) -> None:
        cloud_repr, cloud = cloud_tuple
        assert capabilities is not None
        # cloud_capabilities is a list of (capability, ok, reason)
        # where ok is True if the cloud credentials are valid for the capability
        cloud_capabilities: List[Tuple[sky_cloud.CloudCapability, bool,
                                       Optional[str]]] = []
        for capability in capabilities:
            with rich_utils.safe_status(f'Checking {cloud_repr}...'):
                try:
                    ok, reason = cloud.check_credentials(capability)
                except exceptions.NotSupportedError:
                    continue
                except Exception:  # pylint: disable=broad-except
                    # Catch all exceptions to prevent a single cloud
                    # from blocking the check for other clouds.
                    ok, reason = False, traceback.format_exc()
            cloud_capabilities.append(
                (capability, ok, reason.strip() if reason else None))
            if ok:
                enabled_clouds.setdefault(cloud_repr, []).append(capability)
            else:
                disabled_clouds.setdefault(cloud_repr, []).append(capability)
        _print_checked_cloud(echo, verbose, cloud_tuple, cloud_capabilities)

    def get_cloud_tuple(
            cloud_name: str) -> Tuple[str, Union[sky_clouds.Cloud, ModuleType]]:
        # Validates cloud_name and returns a tuple of the cloud's name and
        # the cloud object. Includes special handling for Cloudflare.
        if cloud_name.lower().startswith('cloudflare'):
            return cloudflare.NAME, cloudflare
        else:
            cloud_obj = registry.CLOUD_REGISTRY.from_str(cloud_name)
            assert cloud_obj is not None, f'Cloud {cloud_name!r} not found'
            return repr(cloud_obj), cloud_obj

    def get_all_clouds():
        return tuple([repr(c) for c in registry.CLOUD_REGISTRY.values()] +
                     [cloudflare.NAME])

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

    # Determine the set of enabled clouds: (previously enabled clouds + newly
    # enabled clouds - newly disabled clouds) intersected with
    # config_allowed_clouds, if specified in config.yaml.
    # This means that if a cloud is already enabled and is not included in
    # allowed_clouds in config.yaml, it will be disabled.
    all_enabled_clouds: Set[str] = set()
    for capability in capabilities:
        # Cloudflare is not a real cloud in registry.CLOUD_REGISTRY, and should
        # not be inserted into the DB (otherwise `sky launch` and other code
        # would error out when it's trying to look it up in the registry).
        enabled_clouds_set = {
            cloud for cloud, capabilities in enabled_clouds.items()
            if capability in capabilities and not cloud.startswith('Cloudflare')
        }
        disabled_clouds_set = {
            cloud for cloud, capabilities in disabled_clouds.items()
            if capability in capabilities and not cloud.startswith('Cloudflare')
        }
        config_allowed_clouds_set = {
            cloud for cloud in config_allowed_cloud_names
            if not cloud.startswith('Cloudflare')
        }
        previously_enabled_clouds_set = {
            repr(cloud)
            for cloud in global_user_state.get_cached_enabled_clouds(capability)
        }
        enabled_clouds_for_capability = (config_allowed_clouds_set & (
            (previously_enabled_clouds_set | enabled_clouds_set) -
            disabled_clouds_set))
        global_user_state.set_enabled_clouds(
            list(enabled_clouds_for_capability), capability)
        all_enabled_clouds = all_enabled_clouds.union(
            enabled_clouds_for_capability)
    disallowed_clouds_hint = None
    if disallowed_cloud_names:
        disallowed_clouds_hint = (
            '\nNote: The following clouds were disabled because they were not '
            'included in allowed_clouds in ~/.sky/skyconfig.yaml: '
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
        clouds_arg = (f' {" ".join(disabled_clouds).lower()}'
                      if clouds is not None else '')
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
                _format_enabled_cloud(cloud, capabilities)
                for cloud, capabilities in enabled_clouds.items()
            ])
            echo(f'\n{colorama.Fore.GREEN}{PARTY_POPPER_EMOJI} '
                 f'Enabled clouds {PARTY_POPPER_EMOJI}'
                 f'{colorama.Style.RESET_ALL}{enabled_clouds_str}')
    return enabled_clouds


def check_capability(
    capability: sky_cloud.CloudCapability,
    quiet: bool = False,
    verbose: bool = False,
    clouds: Optional[Iterable[str]] = None,
) -> List[str]:
    clouds_with_capability = []
    enabled_clouds = check_capabilities(quiet, verbose, clouds, [capability])
    for cloud, capabilities in enabled_clouds.items():
        if capability in capabilities:
            clouds_with_capability.append(cloud)
    return clouds_with_capability


def check(
    quiet: bool = False,
    verbose: bool = False,
    clouds: Optional[Iterable[str]] = None,
) -> List[str]:
    return list(
        check_capabilities(quiet, verbose, clouds,
                           sky_cloud.ALL_CAPABILITIES).keys())


def get_cached_enabled_clouds_or_refresh(
        capability: sky_cloud.CloudCapability,
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
    cached_enabled_clouds = global_user_state.get_cached_enabled_clouds(
        capability)
    if not cached_enabled_clouds:
        try:
            check_capability(sky_cloud.CloudCapability.COMPUTE, quiet=True)
        except SystemExit:
            # If no cloud is enabled, check() will raise SystemExit.
            # Here we catch it and raise the exception later only if
            # raise_if_no_cloud_access is set to True.
            pass
        cached_enabled_clouds = global_user_state.get_cached_enabled_clouds(
            capability)
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
    r2_is_enabled, _ = cloudflare.check_storage_credentials()
    if r2_is_enabled:
        r2_credential_mounts = cloudflare.get_credential_file_mounts()
        file_mounts.update(r2_credential_mounts)
    return file_mounts


def _print_checked_cloud(
    echo: Callable,
    verbose: bool,
    cloud_tuple: Tuple[str, Union[sky_clouds.Cloud, ModuleType]],
    cloud_capabilities: List[Tuple[sky_cloud.CloudCapability, bool,
                                   Optional[str]]],
) -> None:
    """Prints whether a cloud is enabled, and the capabilities that are enabled.
    If any hints (for enabled capabilities) or
    reasons (for disabled capabilities) are provided, they will be printed.

    Args:
        echo: The function to use to print the message.
        verbose: Whether to print the verbose output.
        cloud_tuple: The cloud to print the capabilities for.
        cloud_capabilities: The capabilities for the cloud.
    """

    def _yellow_color(str_to_format: str) -> str:
        return (f'{colorama.Fore.LIGHTYELLOW_EX}'
                f'{str_to_format}'
                f'{colorama.Style.RESET_ALL}')

    cloud_repr, cloud = cloud_tuple
    # Print the capabilities for the cloud.
    # consider cloud enabled if any capability is enabled.
    enabled_capabilities: List[sky_cloud.CloudCapability] = []
    hints_to_capabilities: Dict[str, List[sky_cloud.CloudCapability]] = {}
    reasons_to_capabilities: Dict[str, List[sky_cloud.CloudCapability]] = {}
    for capability, ok, reason in cloud_capabilities:
        if ok:
            enabled_capabilities.append(capability)
            if reason is not None:
                hints_to_capabilities.setdefault(reason, []).append(capability)
        elif reason is not None:
            reasons_to_capabilities.setdefault(reason, []).append(capability)
    status_msg: str = 'disabled'
    styles: Dict[str, Any] = {'dim': True}
    capability_string: str = ''
    activated_account: Optional[str] = None
    if enabled_capabilities:
        status_msg = 'enabled'
        styles = {'fg': 'green', 'bold': False}
        capability_string = f'[{", ".join(enabled_capabilities)}]'
        if verbose and cloud is not cloudflare:
            activated_account = cloud.get_active_user_identity_str()

    echo(
        click.style(f'  {cloud_repr}: {status_msg} {capability_string}',
                    **styles))
    if activated_account is not None:
        echo(f'    Activated account: {activated_account}')
    for reason, caps in hints_to_capabilities.items():
        echo(f'    Hint [{", ".join(caps)}]: {_yellow_color(reason)}')
    for reason, caps in reasons_to_capabilities.items():
        echo(f'    Reason [{", ".join(caps)}]: {reason}')


def _format_enabled_cloud(cloud_name: str,
                          capabilities: List[sky_cloud.CloudCapability]) -> str:
    """Format the summary of enabled cloud and its enabled capabilities.

    Args:
        cloud_name: The name of the cloud.
        capabilities: The capabilities of the cloud.

    Returns:
        A string of the formatted cloud and capabilities.
    """
    cloud_and_capabilities = f'{cloud_name} [{", ".join(capabilities)}]'

    def _green_color(str_to_format: str) -> str:
        return (
            f'{colorama.Fore.GREEN}{str_to_format}{colorama.Style.RESET_ALL}')

    if cloud_name == repr(sky_clouds.Kubernetes()):
        # Get enabled contexts for Kubernetes
        existing_contexts = sky_clouds.Kubernetes.existing_allowed_contexts()
        if not existing_contexts:
            return _green_color(cloud_and_capabilities)

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
            context_info = f'  Allowed contexts:{"".join(contexts_formatted)}'
        else:
            context_info = f'  Active context: {existing_contexts[0]}'

        return (f'{_green_color(cloud_and_capabilities)}\n'
                f'  {colorama.Style.DIM}{context_info}'
                f'{colorama.Style.RESET_ALL}')
    return _green_color(cloud_and_capabilities)
