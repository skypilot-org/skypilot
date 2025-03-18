"""Credential checks: check cloud credentials and enable clouds."""
import enum
import os
import traceback
from types import ModuleType
from typing import Dict, Iterable, List, Optional, Set, Tuple, Union

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


# Declaring CloudCapability as a subclass of str
# allows it to be JSON serializable.
class CloudCapability(str, enum.Enum):
    # Compute capability.
    COMPUTE = 'compute'
    # Storage capability.
    STORAGE = 'storage'


ALL_CAPABILITIES = [CloudCapability.COMPUTE, CloudCapability.STORAGE]


def check(
    quiet: bool = False,
    verbose: bool = False,
    clouds: Optional[Iterable[str]] = None,
    capabilities: Optional[List[CloudCapability]] = None,
) -> Dict[str, List[CloudCapability]]:
    echo = (lambda *_args, **_kwargs: None
           ) if quiet else lambda *args, **kwargs: click.echo(
               *args, **kwargs, color=True)
    echo('Checking credentials to enable clouds for SkyPilot.')
    if capabilities is None:
        capabilities = ALL_CAPABILITIES
    assert capabilities is not None
    enabled_clouds = {}
    disabled_clouds = {}

    def check_credentials(
            cloud: Union[sky_clouds.Cloud, ModuleType],
            capability: CloudCapability) -> Tuple[bool, Optional[str]]:
        if capability == CloudCapability.COMPUTE:
            return cloud.check_credentials()
        elif capability == CloudCapability.STORAGE:
            return cloud.check_storage_credentials()
        else:
            raise ValueError(f'Invalid capability: {capability}')

    def get_cached_state(capability: CloudCapability) -> List[sky_clouds.Cloud]:
        if capability == CloudCapability.COMPUTE:
            return global_user_state.get_cached_enabled_clouds()
        elif capability == CloudCapability.STORAGE:
            return global_user_state.get_cached_enabled_storage_clouds()
        else:
            raise ValueError(f'Invalid capability: {capability}')

    def set_cached_state(clouds: List[str],
                         capability: CloudCapability) -> None:
        if capability == CloudCapability.COMPUTE:
            global_user_state.set_enabled_clouds(clouds)
        elif capability == CloudCapability.STORAGE:
            global_user_state.set_enabled_storage_clouds(clouds)
        else:
            raise ValueError(f'Invalid capability: {capability}')

    def check_one_cloud(cloud_tuple: Tuple[str, Union[sky_clouds.Cloud,
                                                      ModuleType]],
                        capabilities: List[CloudCapability]) -> None:
        cloud_repr, cloud = cloud_tuple
        for capability in capabilities:
            not_supported = False
            with rich_utils.safe_status(f'Checking {cloud_repr}...'):
                try:
                    ok, reason = check_credentials(cloud, capability)
                except exceptions.NotSupportedError:
                    not_supported = True
                except Exception:  # pylint: disable=broad-except
                    # Catch all exceptions to prevent a single cloud
                    # from blocking the check for other clouds.
                    ok, reason = False, traceback.format_exc()
            if not_supported:
                continue
            status_msg = ('enabled' if ok else 'disabled')
            styles = {'fg': 'green', 'bold': False} if ok else {'dim': True}
            echo('  ' + click.style(f'{cloud_repr}: {status_msg}', **styles) +
                 ' ' * 30)
            if ok:
                if cloud_repr not in enabled_clouds:
                    enabled_clouds[cloud_repr] = [capability]
                else:
                    enabled_clouds[cloud_repr].append(capability)
                if verbose and cloud is not cloudflare:
                    activated_account = cloud.get_active_user_identity_str()
                    if activated_account is not None:
                        echo(f'    Activated account: {activated_account}')
                if reason is not None:
                    echo(f'    Hint: {reason}')
            else:
                if cloud_repr not in disabled_clouds:
                    disabled_clouds[cloud_repr] = [capability]
                else:
                    disabled_clouds[cloud_repr].append(capability)
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
        check_one_cloud(cloud_tuple, capabilities)

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
            repr(cloud) for cloud in get_cached_state(capability)
        }
        enabled_clouds_for_capability = (config_allowed_clouds_set & (
            (previously_enabled_clouds_set | enabled_clouds_set) -
            disabled_clouds_set))
        set_cached_state(list(enabled_clouds_for_capability), capability)
        all_enabled_clouds = all_enabled_clouds.union(
            enabled_clouds_for_capability)
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
                _format_enabled_cloud(cloud) for cloud in sorted(enabled_clouds)
            ])
            echo(f'\n{colorama.Fore.GREEN}{PARTY_POPPER_EMOJI} '
                 f'Enabled clouds {PARTY_POPPER_EMOJI}'
                 f'{colorama.Style.RESET_ALL}{enabled_clouds_str}')
    return enabled_clouds


def check_single_capability(
    quiet: bool = False,
    verbose: bool = False,
    clouds: Optional[Iterable[str]] = None,
    capability: CloudCapability = CloudCapability.COMPUTE,
) -> List[str]:
    clouds_with_capability = []
    enabled_clouds = check(quiet, verbose, clouds, [capability])
    for cloud, capabilities in enabled_clouds.items():
        if capability in capabilities:
            clouds_with_capability.append(cloud)
    return clouds_with_capability


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
            check_single_capability(quiet=True,
                                    capability=CloudCapability.COMPUTE)
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


def get_cached_enabled_storage_clouds_or_refresh(
        raise_if_no_cloud_access: bool = False) -> List[sky_clouds.Cloud]:
    """Returns cached enabled storage clouds and if no cloud is enabled,
    refresh.

    This function will perform a refresh if no public cloud is enabled.

    Args:
        raise_if_no_cloud_access: if True, raise an exception if no public
            cloud is enabled.

    Raises:
        exceptions.NoCloudAccessError: if no public cloud is enabled and
            raise_if_no_cloud_access is set to True.
    """
    cached_enabled_storage_clouds = \
        global_user_state.get_cached_enabled_storage_clouds()
    if not cached_enabled_storage_clouds:
        try:
            check_single_capability(quiet=True,
                                    capability=CloudCapability.STORAGE)
        except SystemExit:
            # If no cloud is enabled, check() will raise SystemExit.
            # Here we catch it and raise the exception later only if
            # raise_if_no_cloud_access is set to True.
            pass
        cached_enabled_storage_clouds = \
            global_user_state.get_cached_enabled_storage_clouds()
    if raise_if_no_cloud_access and not cached_enabled_storage_clouds:
        with ux_utils.print_exception_no_traceback():
            raise exceptions.NoCloudAccessError(
                'Cloud access is not set up. Run: '
                f'{colorama.Style.BRIGHT}sky check{colorama.Style.RESET_ALL}')
    return cached_enabled_storage_clouds


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
