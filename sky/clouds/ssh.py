"""SSH Cloud for SkyPilot.

This cloud is a wrapper around Kubernetes that only uses SSH-specific contexts.
It allows users to manage SSH-based Kubernetes clusters defined in ~/.sky/ssh_node_pools.yaml
directly, without having to interact with Kubernetes-specific commands.
"""

import typing
from typing import Dict, List, Optional, Set, Tuple

from sky import exceptions
from sky import skypilot_config
from sky.clouds import kubernetes
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.utils import registry

if typing.TYPE_CHECKING:
    # Renaming to avoid shadowing variables.
    from sky import resources as resources_lib


@registry.CLOUD_REGISTRY.register()
class SSH(kubernetes.Kubernetes):
    """SSH cloud implementation.
    
    This cloud is a wrapper around Kubernetes that only uses contexts starting
    with 'ssh-', which are managed through SkyPilot's SSH deployment mechanism.
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
    def existing_allowed_contexts(cls) -> List[str]:
        """Get existing allowed contexts that start with 'ssh-'.
        
        Override the Kubernetes implementation to only return contexts that
        start with 'ssh-', which are created by the SSH deployment mechanism.
        """
        # Get all contexts from the Kubernetes implementation
        all_contexts = kubernetes_utils.get_all_kube_context_names()
        if not all_contexts:
            return []
        
        all_contexts = set(all_contexts)
        
        # Filter for SSH contexts (those starting with 'ssh-')
        ssh_contexts = [context for context in all_contexts if context.startswith('ssh-')]
        
        # If allowed_contexts is specified in the config, filter to those
        allowed_contexts = skypilot_config.get_nested(
            ('ssh', 'allowed_contexts'), None)
        
        if allowed_contexts is not None:
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
        
        # If no allowed_contexts specified, return all SSH contexts
        return ssh_contexts
    
    @classmethod
    def _check_compute_credentials(cls) -> Tuple[bool, Optional[str]]:
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
        except Exception as e:
            return (False, f'Failed to get SSH contexts: {str(e)}')
        
        if not existing_allowed_contexts:
            return (False, 'No SSH clusters found. Run "sky ssh up" to create one.')
        
        # Check credentials for each context
        reasons = []
        hints = []
        success = False
        for context in existing_allowed_contexts:
            try:
                check_result = kubernetes_utils.check_credentials(
                    context, run_optional_checks=True)
                if check_result[0]:
                    success = True
                    if check_result[1] is not None:
                        hints.append(f'SSH cluster {context}: {check_result[1]}')
                else:
                    reasons.append(f'SSH cluster {context}: {check_result[1]}')
            except Exception as e:
                reasons.append(f'SSH cluster {context}: {str(e)}')
        
        if success:
            return (True, cls._format_credential_check_results(hints, reasons))
        
        return (False, 'Failed to find available SSH clusters with working '
                'credentials. Run "sky ssh up" to create one, or check details:\n' + 
                '\n'.join(reasons)) 