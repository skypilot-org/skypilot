"""Flux Framework.

Flux is implemented via the Flux Operator in Kubernetes. To make
it maximally flexible to deploy to different clouds, we use the
same Kubernetes abstractions, but also deploy the Flux Operator.
We do this because it is unlikely to have a cluster with Flux
already running to provision to.
"""
import typing
from typing import Dict, List, Optional, Tuple

from sky import clouds
from sky import sky_logging
from sky.clouds.kubernetes import Kubernetes
from sky.utils import resources_utils

if typing.TYPE_CHECKING:
    # Renaming to avoid shadowing variables.
    from sky import resources as resources_lib

logger = sky_logging.init_logger(__name__)


@clouds.CLOUD_REGISTRY.register
class Flux(Kubernetes):
    """Flux Framework"""

    _REPR = 'Flux'

    def make_deploy_resources_variables(
            self,
            resources: 'resources_lib.Resources',
            cluster_name: resources_utils.ClusterName,
            region: Optional['clouds.Region'],
            zones: Optional[List['clouds.Zone']],
            dryrun: bool = False) -> Dict[str, Optional[str]]:
        """Make deployment YAML variables

        The "module" parameter needs to be populated with the custom
        Flux provisioner, tweaking from Kubernetes.
        """
        deploy_vars = super(Flux, self).make_deploy_resources_variables(
            resources, cluster_name, region, zones, dryrun)
        # Disabled until provision module is added
        deploy_vars['module'] = 'sky.provision.flux'
        return deploy_vars

    def _get_feasible_launchable_resources(
        self, resources: 'resources_lib.Resources'
    ) -> Tuple[List['resources_lib.Resources'], List[str]]:
        """Get feasible launchable resources

        This function sets the cloud on the resources, so we need
        to retrieve from Kubernetes and update "Kubernetes" to "Flux"
        """
        # This returns a tuple with feasible resources/candidate list, unwrap
        res = super(Flux, self)._get_feasible_launchable_resources(resources)
        (feasible_resources, fuzzy_candidate_list) = res

        # Update the cloud, Kubernetes -> Flux since will be printed in UI
        for item in feasible_resources:
            item.set_cloud(self)
        return (feasible_resources, fuzzy_candidate_list)
