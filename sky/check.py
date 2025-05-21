"""Credential checks: check cloud credentials and enable clouds."""
import collections
import itertools
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
from sky.utils import subprocess_utils
from sky.utils import ux_utils

CHECK_MARK_EMOJI = '\U00002714'  # Heavy check mark unicode
PARTY_POPPER_EMOJI = '\U0001F389'  # Party popper unicode


def check_capabilities(
    quiet: bool = False,
    verbose: bool = False,
    clouds: Optional[Iterable[str]] = None,
    capabilities: Optional[List[sky_cloud.CloudCapability]] = None,
    workspace: Optional[str] = None,
) -> Dict[str, Dict[str, List[sky_cloud.CloudCapability]]]:
    echo = (lambda *_args, **_kwargs: None
           ) if quiet else lambda *args, **kwargs: click.echo(
               *args, **kwargs, color=True)

    if capabilities is None:
        capabilities_to_check = sky_cloud.ALL_CAPABILITIES
    else:
        capabilities_to_check = capabilities
    assert capabilities_to_check is not None

    def _get_all_cloud_names_for_registry() -> Tuple[str, ...]:
        return tuple([repr(c) for c in registry.CLOUD_REGISTRY.values()] +
                     [cloudflare.NAME])

    def _execute_check_logic_for_workspace(
        current_workspace_name: str,
        hide_per_cloud_details: bool,
        is_single_specified_check_run: bool,
        original_active_workspace_name: str,
    ) -> Tuple[Dict[str, List[sky_cloud.CloudCapability]], bool]:
        nonlocal echo, verbose, clouds, quiet

        enabled_clouds_for_workspace: Dict[
            str, List[sky_cloud.CloudCapability]] = {}
        disabled_clouds_for_workspace: Dict[
            str, List[sky_cloud.CloudCapability]] = {}

        def check_one_cloud_one_capability(
            payload: Tuple[Tuple[str, Union[sky_clouds.Cloud, ModuleType]],
                           sky_cloud.CloudCapability]
        ) -> Optional[Tuple[sky_cloud.CloudCapability, bool, Optional[str]]]:
            cloud_tuple, capability_item = payload
            _, cloud_obj = cloud_tuple
            try:
                ok, reason = cloud_obj.check_credentials(capability_item)
            except exceptions.NotSupportedError:
                return None
            except Exception:  # pylint: disable=broad-except
                ok, reason = False, traceback.format_exc()
            return capability_item, ok, reason.strip() if reason else None

        def get_cloud_tuple(
                cloud_name: str
        ) -> Tuple[str, Union[sky_clouds.Cloud, ModuleType]]:
            if cloud_name.lower().startswith('cloudflare'):
                return cloudflare.NAME, cloudflare
            else:
                cloud_obj = registry.CLOUD_REGISTRY.from_str(cloud_name)
                assert cloud_obj is not None, f'Cloud {cloud_name!r} not found'
                return repr(cloud_obj), cloud_obj

        if clouds is not None:
            cloud_list_to_check = clouds
        else:
            cloud_list_to_check = _get_all_cloud_names_for_registry()

        clouds_to_check_tuples = [
            get_cloud_tuple(c) for c in cloud_list_to_check
        ]

        config_allowed_cloud_names = sorted([
            get_cloud_tuple(c)[0] for c in skypilot_config.get_nested((
                'allowed_clouds',), _get_all_cloud_names_for_registry())
        ])

        workspace_disabled_clouds_config = []
        for cloud_name_str in config_allowed_cloud_names:
            cloud_config = skypilot_config.get_workspace_cloud(
                cloud_name_str, workspace=current_workspace_name)
            cloud_disabled = cloud_config.get('disabled', False)
            if cloud_disabled:
                workspace_disabled_clouds_config.append(cloud_name_str)

        config_allowed_cloud_names = [
            c for c in config_allowed_cloud_names
            if c not in workspace_disabled_clouds_config
        ]

        clouds_to_check_tuples_filtered = [
            c for c in clouds_to_check_tuples
            if c[0] in config_allowed_cloud_names
        ]

        combinations = list(
            itertools.product(clouds_to_check_tuples_filtered,
                              capabilities_to_check))

        if not combinations:
            echo(
                click.style(
                    'No clouds to check for workspace '
                    f'{current_workspace_name}. '
                    'This could be due to `allowed_clouds` or workspace '
                    'configurations.',
                    fg='yellow'))
            return {}, False

        with rich_utils.safe_status(
                ux_utils.spinner_message(
                    f'Checking infra choices for workspace: {current_workspace_name}...'
                )):
            check_results = subprocess_utils.run_in_parallel(
                check_one_cloud_one_capability, combinations)

        check_results_dict: Dict[Tuple[str, Union[sky_clouds.Cloud,
                                                  ModuleType]],
                                 List[Tuple[sky_cloud.CloudCapability, bool,
                                            Optional[str]]]] = (
                                                collections.defaultdict(list))

        for combination_item, check_result_item in zip(combinations,
                                                       check_results):
            if check_result_item is None:
                continue
            capability_item, ok, _ = check_result_item
            cloud_tuple_item, _ = combination_item
            cloud_repr = cloud_tuple_item[0]
            if ok:
                enabled_clouds_for_workspace.setdefault(
                    cloud_repr, []).append(capability_item)
            else:
                disabled_clouds_for_workspace.setdefault(
                    cloud_repr, []).append(capability_item)
            check_results_dict[cloud_tuple_item].append(check_result_item)

        if not hide_per_cloud_details:
            for cloud_tuple_item, check_result_list_item in sorted(
                    check_results_dict.items(), key=lambda item: item[0][0]):
                _print_checked_cloud(echo, verbose, cloud_tuple_item,
                                     check_result_list_item)

        all_enabled_clouds_for_workspace_set: Set[str] = set()
        for capability_item in capabilities_to_check:
            enabled_clouds_set = {
                cloud for cloud, caps in enabled_clouds_for_workspace.items() if
                capability_item in caps and not cloud.startswith('Cloudflare')
            }
            disabled_clouds_set = {
                cloud for cloud, caps in disabled_clouds_for_workspace.items()
                if capability_item in caps and
                not cloud.startswith('Cloudflare')
            }
            config_allowed_clouds_set = {
                cloud for cloud in config_allowed_cloud_names
                if not cloud.startswith('Cloudflare')
            }
            previously_enabled_clouds_set = {
                repr(cloud_obj)
                for cloud_obj in global_user_state.get_cached_enabled_clouds(
                    capability_item, current_workspace_name)
            }

            enabled_clouds_for_capability = (config_allowed_clouds_set & (
                (previously_enabled_clouds_set | enabled_clouds_set) -
                disabled_clouds_set))

            global_user_state.set_enabled_clouds(
                list(enabled_clouds_for_capability), capability_item,
                current_workspace_name)
            all_enabled_clouds_for_workspace_set = (
                all_enabled_clouds_for_workspace_set.union(
                    enabled_clouds_for_capability))

        has_any_enabled_clouds = bool(all_enabled_clouds_for_workspace_set)

        if not has_any_enabled_clouds:
            is_critical_failure_point = (
                is_single_specified_check_run or
                (not is_single_specified_check_run and
                 current_workspace_name == original_active_workspace_name))
            msg_color = 'red' if is_critical_failure_point else 'yellow'
            msg = (
                f'No cloud is enabled for workspace '
                f'{click.style(current_workspace_name, bold=True)}. '
                'SkyPilot will not be able to run any task in this workspace. '
                'Run `sky check` for more info.')
            echo(click.style(msg, fg=msg_color, bold=True))
        else:
            if not quiet:
                enabled_clouds_str = '\n  ' + '\n  '.join([
                    _format_enabled_cloud(cloud_name_str, caps)
                    for cloud_name_str, caps in sorted(
                        enabled_clouds_for_workspace.items(),
                        key=lambda item: item[0])
                ])
                echo(
                    f'\n{colorama.Fore.GREEN}{PARTY_POPPER_EMOJI} '
                    f'Enabled infra for workspace: {click.style(current_workspace_name, bold=True)} {PARTY_POPPER_EMOJI}'
                    f'{colorama.Style.RESET_ALL}{enabled_clouds_str}')

        return enabled_clouds_for_workspace, has_any_enabled_clouds

    # --- Main check_capabilities logic ---
    initial_original_active_workspace = skypilot_config.get_active_workspace()
    all_workspaces_results: Dict[str,
                                 Dict[str,
                                      List[sky_cloud.CloudCapability]]] = {}
    any_workspace_had_actionable_enabled_clouds = False

    if workspace is not None:
        # Check only the specified workspace
        available_workspaces = skypilot_config.get_workspaces()
        if workspace not in available_workspaces:
            echo(
                click.style(
                    f'Workspace {click.style(workspace, bold=True)} not found in SkyPilot configuration. '
                    f'Available workspaces: {", ".join(available_workspaces.keys())}',
                    fg='red'))
            raise SystemExit(1)

        hide_per_cloud_details_flag = False  # Always show details for single specified check (if verbose)
        with skypilot_config.with_active_workspace(workspace):
            skypilot_config.safe_reload_config()
            enabled_ws_clouds, has_any = _execute_check_logic_for_workspace(
                workspace, hide_per_cloud_details_flag, True,
                initial_original_active_workspace)
            all_workspaces_results[workspace] = enabled_ws_clouds
            if has_any:
                any_workspace_had_actionable_enabled_clouds = True
            else:
                # SystemExit is raised here because it's a specific request that failed
                raise SystemExit()
        # Restore original active workspace context, critical if sky.check is called as a library
        with skypilot_config.with_active_workspace(
                initial_original_active_workspace):
            skypilot_config.safe_reload_config()
    else:
        # Check all workspaces
        workspaces_to_check = list(skypilot_config.get_workspaces().keys())
        if not workspaces_to_check:
            echo(click.style('No workspaces found in config.', fg='yellow'))
            # No return here, global hint and SystemExit for original_active_workspace still apply

        num_workspaces_being_checked_in_run = len(workspaces_to_check)
        hide_per_cloud_details_flag = (not verbose and
                                       num_workspaces_being_checked_in_run > 1)

        original_active_workspace_processed = False
        original_active_workspace_had_clouds = False

        for ws_name in workspaces_to_check:
            with skypilot_config.with_active_workspace(ws_name):
                skypilot_config.safe_reload_config()
                enabled_ws_clouds, has_any = _execute_check_logic_for_workspace(
                    ws_name, hide_per_cloud_details_flag, False,
                    initial_original_active_workspace)
                all_workspaces_results[ws_name] = enabled_ws_clouds
                if has_any:
                    any_workspace_had_actionable_enabled_clouds = True
                if ws_name == initial_original_active_workspace:
                    original_active_workspace_processed = True
                    if has_any:
                        original_active_workspace_had_clouds = True

        # Restore original active workspace context
        with skypilot_config.with_active_workspace(
                initial_original_active_workspace):
            skypilot_config.safe_reload_config()

        # SystemExit logic for multi-workspace check, focusing on the initial_original_active_workspace
        if not workspaces_to_check and not original_active_workspace_processed:  # No workspaces configured at all
            echo(
                click.style(
                    f'No workspaces configured. The default workspace '
                    f'{click.style(initial_original_active_workspace, bold=True)} implicitly has no enabled clouds. '
                    'SkyPilot may not function correctly.',
                    fg='red',
                    bold=True))
            raise SystemExit()
        elif initial_original_active_workspace not in workspaces_to_check and not original_active_workspace_processed:
            echo(
                click.style(
                    f'The originally active workspace '
                    f'{click.style(initial_original_active_workspace, bold=True)} '
                    f'was not found in the configuration to check and thus has no enabled clouds. '
                    'SkyPilot may not function correctly.',
                    fg='red',
                    bold=True))
            raise SystemExit()
        elif original_active_workspace_processed and not original_active_workspace_had_clouds:
            # The specific error for this workspace (in red) was already printed by the helper.
            # This is a more generic final message about the consequence.
            echo(
                click.style(
                    f'The originally active workspace '
                    f'{click.style(initial_original_active_workspace, bold=True)} has no enabled clouds. '
                    'SkyPilot may not function correctly.',
                    fg='red',
                    bold=True))
            raise SystemExit()

    # Global "To enable a cloud..." message, printed once if relevant
    if any_workspace_had_actionable_enabled_clouds and not quiet:
        echo(
            click.style(
                '\nTo enable a cloud, follow the hints above and rerun: ',
                dim=True) +
            click.style('sky check', bold=True) +  # Generic command
            '\n' + click.style(
                'If any problems remain, refer to detailed docs at: '
                'https://docs.skypilot.co/en/latest/getting-started/installation.html',
                dim=True))

    return all_workspaces_results


def check_capability(
    capability: sky_cloud.CloudCapability,
    quiet: bool = False,
    verbose: bool = False,
    clouds: Optional[Iterable[str]] = None,
    workspace: Optional[str] = None,
) -> Dict[str, List[str]]:
    clouds_with_capability = collections.defaultdict(list)
    workspace_enabled_clouds = check_capabilities(quiet, verbose, clouds,
                                                  [capability], workspace)
    for workspace, enabled_clouds in workspace_enabled_clouds.items():
        for cloud, capabilities in enabled_clouds.items():
            if capability in capabilities:
                clouds_with_capability[workspace].append(cloud)
    return clouds_with_capability


def check(
    quiet: bool = False,
    verbose: bool = False,
    clouds: Optional[Iterable[str]] = None,
    workspace: Optional[str] = None,
) -> Dict[str, List[str]]:
    enabled_clouds_by_workspace: Dict[str,
                                      List[str]] = collections.defaultdict(list)
    capabilities_result = check_capabilities(quiet, verbose, clouds,
                                             sky_cloud.ALL_CAPABILITIES,
                                             workspace)
    for ws_name, enabled_clouds_with_caps in capabilities_result.items():
        # For each workspace, get a list of cloud names that have any capabilities enabled.
        # The inner dict enabled_clouds_with_caps maps cloud_name to List[CloudCapability].
        # If the list of capabilities is non-empty, the cloud is considered enabled.
        # We are interested in the keys (cloud names) of this dict if their value (list of caps) is not empty.
        # However, check_capabilities already ensures that only clouds with *some* enabled capabilities
        # (from the ones being checked, i.e. ALL_CAPABILITIES here) are included in its return value.
        # So, the keys of enabled_clouds_with_caps are the enabled cloud names for that workspace.
        enabled_clouds_by_workspace[ws_name] = list(
            enabled_clouds_with_caps.keys())
    return enabled_clouds_by_workspace


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
        capability, skypilot_config.get_active_workspace())
    if not cached_enabled_clouds:
        try:
            check_capability(sky_cloud.CloudCapability.COMPUTE, quiet=True)
        except SystemExit:
            # If no cloud is enabled, check() will raise SystemExit.
            # Here we catch it and raise the exception later only if
            # raise_if_no_cloud_access is set to True.
            pass
        cached_enabled_clouds = global_user_state.get_cached_enabled_clouds(
            capability, skypilot_config.get_active_workspace())
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
