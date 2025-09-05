"""SSH Node Pools"""

import os
import typing
from typing import Dict, List, Optional, Set, Tuple, Union

from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import kubernetes as kubernetes_adaptor
from sky.clouds import kubernetes
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.utils import annotations
from sky.utils import common_utils
from sky.utils import registry
from sky.utils import yaml_utils

if typing.TYPE_CHECKING:
    # Renaming to avoid shadowing variables.
    from sky import resources as resources_lib

logger = sky_logging.init_logger(__name__)

SSH_NODE_POOLS_PATH = os.path.expanduser('~/.sky/ssh_node_pools.yaml')


@registry.CLOUD_REGISTRY.register()
class SSH(kubernetes.Kubernetes):
    """SSH cloud implementation.

    This is used by SSH Node Pools in SkyPilot, which use Kubernetes to manage
    the SSH clusters.

    This cloud is a thin wrapper around Kubernetes that only uses contexts
    starting with 'ssh-', which are managed through `sky ssh up` command.
    """

    _REPR = 'SSH'

    # Keep track of contexts that have been logged as unreachable
    logged_unreachable_contexts: Set[str] = set()

    def __repr__(self):
        return self._REPR

    @classmethod
    def _unsupported_features_for_resources(
        cls, resources: 'resources_lib.Resources'
    ) -> Dict[kubernetes.clouds.CloudImplementationFeatures, str]:
        # Inherit all Kubernetes unsupported features
        return super()._unsupported_features_for_resources(resources)

    @classmethod
    def get_ssh_node_pool_contexts(cls) -> List[str]:
        """Get context names from ssh_node_pools.yaml file.

        Reads the SSH node pools configuration file and returns
        a list of context names by prepending 'ssh-' to each Node Pool name.

        Returns:
            A list of SSH Kubernetes context names derived from the Node Pools
            in the SSH node pools file.
        """
        contexts = []

        if os.path.exists(SSH_NODE_POOLS_PATH):
            try:
                with open(SSH_NODE_POOLS_PATH, 'r', encoding='utf-8') as f:
                    ssh_config = yaml_utils.safe_load(f)
                    if ssh_config:
                        # Get cluster names and prepend 'ssh-' to match
                        # context naming convention
                        contexts = [
                            f'ssh-{cluster_name}'
                            for cluster_name in ssh_config.keys()
                        ]
            except Exception:  # pylint: disable=broad-except
                # If there's an error reading the file, return empty list
                pass

        return contexts

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        if region == kubernetes_adaptor.in_cluster_context_name():
            # If running incluster, we set region to IN_CLUSTER_REGION
            # since there is no context name available.
            return region, zone

        all_contexts = self.existing_allowed_contexts()

        if region is not None and region not in all_contexts:
            region_name = common_utils.removeprefix(region, 'ssh-')
            available_contexts = [
                common_utils.removeprefix(c, 'ssh-') for c in all_contexts
            ]
            err_str = (f'SSH Node Pool {region_name!r} is not set up. '
                       'Run `sky check` for more details. ')
            if available_contexts:
                err_str += f'Available node pools: {available_contexts}'
            raise ValueError(err_str)
        if zone is not None:
            raise ValueError('SSH Node Pools do not support setting zone.')
        return region, zone

    @classmethod
    @annotations.lru_cache(scope='global', maxsize=1)
    def _ssh_log_skipped_contexts_once(
            cls, skipped_contexts: Tuple[str, ...]) -> None:
        """Log skipped contexts for only once.

        We don't directly cache the result of _filter_existing_allowed_contexts
        as the admin policy may update the allowed contexts.
        """
        if skipped_contexts:
            count = len(set(skipped_contexts))
            is_singular = count == 1
            logger.warning(
                f'SSH Node {("Pool" if is_singular else "Pools")} '
                f'{set(skipped_contexts)!r} specified in '
                f'{SSH_NODE_POOLS_PATH} {("has" if is_singular else "have")} '
                'not been set up. Skipping '
                f'{("that pool" if is_singular else "those pools")}. '
                'Run `sky ssh up` to set up.')

    @classmethod
    def existing_allowed_contexts(cls, silent: bool = False) -> List[str]:
        """Get existing allowed contexts that start with 'ssh-'.

        Override the Kubernetes implementation to only return contexts that
        start with 'ssh-', which are created by `sky ssh up`.

        Returns contexts based on clusters defined in ~/.sky/ssh_node_pools.yaml
        """
        # Get all contexts from the Kubernetes implementation
        all_contexts = kubernetes_utils.get_all_kube_context_names()
        if not all_contexts:
            return []

        all_contexts = set(all_contexts)

        # Workspace-level allowed_node_pools should take precedence over
        # the global allowed_node_pools.
        allowed_node_pools = skypilot_config.get_workspace_cloud('ssh').get(
            'allowed_node_pools', None)
        if allowed_node_pools is None:
            allowed_node_pools = skypilot_config.get_effective_region_config(
                cloud='ssh',
                region=None,
                keys=('allowed_node_pools',),
                default_value=None)

        # Filter for SSH contexts (those starting with 'ssh-')
        ssh_contexts = [
            context for context in all_contexts if context.startswith('ssh-')
        ]

        # Get contexts from SSH node pools file
        all_node_pool_contexts = cls.get_ssh_node_pool_contexts()

        def filter_by_allowed_node_pools(ctxs):
            if allowed_node_pools is None:
                return ctxs
            return [
                ctx for ctx in ctxs
                if common_utils.removeprefix(ctx, 'ssh-') in allowed_node_pools
            ]

        if all_node_pool_contexts:
            # Only include allowed contexts that exist
            existing_contexts = []
            skipped_contexts = []
            for context in all_node_pool_contexts:
                if context in ssh_contexts:
                    existing_contexts.append(context)
                else:
                    skipped_contexts.append(context)
            if not silent:
                cls._ssh_log_skipped_contexts_once(tuple(skipped_contexts))
            return filter_by_allowed_node_pools(existing_contexts)

        # If no all_node_pool_contexts found, return all SSH contexts
        return filter_by_allowed_node_pools(ssh_contexts)

    @classmethod
    def _check_compute_credentials(
            cls) -> Tuple[bool, Optional[Union[str, Dict[str, str]]]]:
        """Check if the user has access credentials to SSH contexts."""
        # Check for port forward dependencies - reuse Kubernetes implementation
        reasons = kubernetes_utils.check_port_forward_mode_dependencies(False)
        if reasons is not None:
            formatted = '\n'.join(
                [reasons[0]] +
                [f'{cls._INDENT_PREFIX}' + r for r in reasons[1:]])
            return (False, formatted)

        # Get SSH contexts
        try:
            existing_allowed_contexts = cls.existing_allowed_contexts()
        except Exception as e:  # pylint: disable=broad-except
            return (False, f'Failed to get SSH contexts: {str(e)}')

        if not existing_allowed_contexts:
            return (False,
                    'No SSH Node Pools are up. Run `sky ssh up` to set up '
                    f'Node Pools from {SSH_NODE_POOLS_PATH}.')

        # Check credentials for each context
        ctx2text = {}
        success = False
        for context in existing_allowed_contexts:
            suc, text = super()._check_single_context(context)
            success = success or suc
            ctx2text[context] = text

        return success, ctx2text

    @classmethod
    def check_single_context(cls, context: str) -> Tuple[bool, str]:
        """Checks if the context is valid and accessible."""
        reasons = kubernetes_utils.check_port_forward_mode_dependencies(False)
        if reasons is not None:
            formatted = '\n'.join(
                [reasons[0]] +
                [f'{cls._INDENT_PREFIX}' + r for r in reasons[1:]])
            return (False, formatted)

        # Add ssh- prefix to the context
        if not context.startswith('ssh-'):
            context = f'ssh-{context}'

        # Get SSH contexts
        try:
            existing_allowed_contexts = cls.existing_allowed_contexts()
        except Exception as e:  # pylint: disable=broad-except
            return (False, f'Failed to get SSH contexts: {str(e)}')

        if not existing_allowed_contexts:
            return (False,
                    'No SSH Node Pools are up. Run `sky ssh up` to set up '
                    f'Node Pools from {SSH_NODE_POOLS_PATH}.')

        if context not in existing_allowed_contexts:
            return (False, f'SSH Node Pool {context} is not set up. '
                    f'Run `sky ssh up --infra {context}` to set up.')

        # Check if the context is valid
        suc, text = super()._check_single_context(context)
        if not suc:
            return (False, text)

        return (True, 'SSH Node Pool is set up.')

    @classmethod
    def expand_infras(cls) -> List[str]:
        return [
            f'{cls.canonical_name()}/{c.lstrip("ssh-")}'
            for c in cls.existing_allowed_contexts(silent=True)
        ]

    @classmethod
    def display_name(cls) -> str:
        return 'SSH Node Pools'
