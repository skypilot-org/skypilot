"""SSH Node Pools"""

import os
import typing
from typing import Dict, List, Optional, Set, Tuple, Union

import yaml

from sky.clouds import kubernetes
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.utils import registry

if typing.TYPE_CHECKING:
    # Renaming to avoid shadowing variables.
    from sky import resources as resources_lib

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
                    ssh_config = yaml.safe_load(f)
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

    @classmethod
    def existing_allowed_contexts(cls) -> List[str]:
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

        # Filter for SSH contexts (those starting with 'ssh-')
        ssh_contexts = [
            context for context in all_contexts if context.startswith('ssh-')
        ]

        # Get contexts from SSH node pools file
        allowed_contexts = cls.get_ssh_node_pool_contexts()

        if allowed_contexts:
            # Only include allowed contexts that exist
            existing_contexts = []
            skipped_contexts = []
            for context in allowed_contexts:
                if context in ssh_contexts:
                    existing_contexts.append(context)
                else:
                    skipped_contexts.append(context)
            cls._log_skipped_contexts_once(tuple(skipped_contexts))
            return existing_contexts

        # If no allowed_contexts found, return all SSH contexts
        return ssh_contexts

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
                    'No SSH clusters found. Run "sky ssh up" to create one.')

        # Check credentials for each context
        ctx2text = {}
        success = False
        for context in existing_allowed_contexts:
            suc, text = super()._check_single_context(context)
            success = success or suc
            ctx2text[context] = text

        return success, ctx2text
