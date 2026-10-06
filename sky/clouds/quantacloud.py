"""QuantaCloud cloud for SkyPilot.

QuantaCloud (https://quantacloud.net) rents on-demand NVIDIA GPU **VMs**
(deploymentType ``virtual``, despite the console's "Bare Metal" template
name) from a prepaid credit balance. Every API deployment boots the single
stock image (Ubuntu 22.04 + NVIDIA drivers + Docker) with the account's SSH
keys injected, reached over direct SSH on port 22 — no NAT port mapping
(unlike Vast), no per-upstream provider semantics (unlike the Spheron and
Shadeform brokers).

Instance types are synthesized tokens ``<gpu-slug>:<count>`` (e.g.
``rtx-pro-6000-blackwell:1``): the provider's offers are per (GPU model x
count x region x price) with EPHEMERAL UUIDs — an out-of-stock offer 404s —
so the catalog row carries the stable slug+count pair and the provisioner
re-resolves the concrete offer UUID from the live list at launch (see
``sky/provision/quantacloud/instance.py``). ``Region`` is the provider's own
region token (``us-east-1``, ``us-midwest-1``...); QuantaCloud has no zone
concept below the region.

Modeled on ``sky/clouds/latitude.py``. Deliberate differences, each
grounded in the QuantaCloud API (https://docs.quantacloud.net/llms.txt):

* **STOP is unsupported.** Stopping a deployment TERMINATES it and deletes
  its disk — there is no stop-and-keep state at all, so a "stop" that
  quietly destroys (or keeps billing) would be the worst of both.
  Terminate is the only teardown.
* **Spot is unsupported.** Billing is prepaid-credit hourly only; there is
  no interruptible tier.
* **SSH keys are registered per-deployment** by the provisioner
  (``POST /users/me/ssh-keys``, matched by fingerprint derived from key
  material), so there is no account-level upload for an auth hook to
  perform — same as Spheron/Latitude.
"""

from __future__ import annotations

import os
import typing
from typing import Any, Dict, List, Optional, Tuple, Union

from sky import clouds
from sky.catalog import quantacloud_catalog
from sky.utils import registry
from sky.utils import resources_utils

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib

# The credential the provisioner needs on the machine running SkyPilot.
_CREDENTIAL_FILES = ['credentials']


@registry.CLOUD_REGISTRY.register
class Quantacloud(clouds.Cloud):
    """QuantaCloud on-demand GPU VM cloud."""

    # `_REPR` IS THE CATALOG NAME, not a display string. The
    # `Cloud.validate_region_zone` hook
    # passes `clouds=cls._REPR.lower()`, which SkyPilot turns into
    # `sky.catalog.{name}_catalog`. Inheriting the ABC's placeholder gives
    # `'<cloud>'` and every launch dies with
    #     ValueError: Cannot find module "sky.catalog.<cloud>_catalog"
    # — the bug that killed every Shadeform launch until
    # urun-sh/skypilot#1 added `Shadeform._REPR` (see the skypilot-controller
    # Dockerfile's fork-pin comment).
    _REPR = 'Quantacloud'

    _MAX_CLUSTER_NAME_LEN_LIMIT = 120

    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.STOP:
            ('stopping a QuantaCloud deployment terminates it and deletes '
             'its disk — there is no stop-and-keep state — so stop is '
             'refused rather than silently destroy.'),
        clouds.CloudImplementationFeatures.SPOT_INSTANCE:
            ('QuantaCloud bills prepaid-credit hourly only; there is no '
             'interruptible/spot tier.'),
        clouds.CloudImplementationFeatures.MULTI_NODE:
            ('Multi-node clusters are not supported on QuantaCloud.'),
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER:
            ('Offers ship a fixed included disk; disk tier is not selectable.'),
        clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER:
            ('Network tier is not selectable on QuantaCloud.'),
        clouds.CloudImplementationFeatures.STORAGE_MOUNTING:
            ('Object storage mounting is not supported on QuantaCloud.'),
        clouds.CloudImplementationFeatures.HOST_CONTROLLERS:
            ('Host controllers are not supported on QuantaCloud.'),
        clouds.CloudImplementationFeatures.HIGH_AVAILABILITY_CONTROLLERS:
            ('High availability controllers are not supported on QuantaCloud.'),
        clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER:
            ('Disk cloning is not supported on QuantaCloud (stopping deletes '
             'the disk; there are no volumes or snapshots).'),
        clouds.CloudImplementationFeatures.IMAGE_ID:
            ('Every API deployment boots the single stock image (Ubuntu '
             '22.04 + NVIDIA drivers + Docker); an arbitrary image id cannot '
             'be requested.'),
        clouds.CloudImplementationFeatures.DOCKER_IMAGE:
            ('The stock image boots the OS directly; there is no docker '
             'image selection on QuantaCloud.'),
        clouds.CloudImplementationFeatures.CUSTOM_MULTI_NETWORK:
            ('Custom multi-network is not supported on QuantaCloud.'),
        clouds.CloudImplementationFeatures.LOCAL_DISK:
            ('Local disk is not selectable on QuantaCloud.'),
    }

    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT
    OPEN_PORTS_VERSION = clouds.OpenPortsVersion.LAUNCH_ONLY

    @classmethod
    def _unsupported_features_for_resources(
        cls,
        resources: 'resources_lib.Resources',
        region: Optional[str] = None,
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        del resources, region
        return cls._CLOUD_UNSUPPORTED_FEATURES

    @classmethod
    def _max_cluster_name_length(cls) -> Optional[int]:
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    def __repr__(self):
        return 'Quantacloud'

    # -- catalog-backed lookups -------------------------------------------

    @classmethod
    def regions_with_offering(
        cls,
        instance_type: str,
        accelerators: Optional[Dict[str, int]],
        use_spot: bool,
        region: Optional[str],
        zone: Optional[str],
        resources: Optional['resources_lib.Resources'] = None,
    ) -> List[clouds.Region]:
        del accelerators, resources
        assert zone is None, 'QuantaCloud does not support zones.'
        regions = quantacloud_catalog.get_region_zones_for_instance_type(
            instance_type, use_spot)
        if region is not None:
            regions = [r for r in regions if r.name == region]
        return regions

    @classmethod
    def zones_provision_loop(
        cls,
        *,
        region: str,
        num_nodes: int,
        instance_type: str,
        accelerators: Optional[Dict[str, int]] = None,
        use_spot: bool = False,
    ) -> typing.Iterator[None]:
        # One yield PER region that has an offering (the base contract): a
        # single yield for "any region" turned a first-region capacity
        # failure into the end of provisioning on the Spheron lane
        # (urun-sh/skypilot#5/#6).
        del num_nodes
        regions = cls.regions_with_offering(instance_type,
                                            accelerators,
                                            use_spot,
                                            zone=None,
                                            region=region)
        for r in regions:
            assert r.zones is None, r
            yield r.zones

    @classmethod
    def get_vcpus_mem_from_instance_type(
            cls, instance_type: str) -> Tuple[Optional[float], Optional[float]]:
        return quantacloud_catalog.get_vcpus_mem_from_instance_type(
            instance_type)

    @classmethod
    def get_accelerators_from_instance_type(
            cls, instance_type: str) -> Optional[Dict[str, Union[int, float]]]:
        return quantacloud_catalog.get_accelerators_from_instance_type(
            instance_type)

    @classmethod
    def get_default_instance_type(
        cls,
        cpus: Optional[str] = None,
        memory: Optional[str] = None,
        disk_tier: Optional[Any] = None,
        local_disk: Optional[Any] = None,
        region: Optional[str] = None,
        zone: Optional[str] = None,
        use_spot: bool = False,
        max_hourly_cost: Optional[float] = None,
    ) -> Optional[str]:
        del disk_tier, local_disk
        return quantacloud_catalog.get_default_instance_type(
            cpus=cpus,
            memory=memory,
            region=region,
            zone=zone,
            use_spot=use_spot,
            max_hourly_cost=max_hourly_cost,
        )

    def instance_type_exists(self, instance_type: str) -> bool:
        return quantacloud_catalog.instance_type_exists(instance_type)

    def instance_type_to_hourly_cost(
        self,
        instance_type: str,
        use_spot: bool = False,
        region: Optional[str] = None,
        zone: Optional[str] = None,
    ) -> float:
        return quantacloud_catalog.get_hourly_cost(instance_type,
                                                   use_spot=use_spot,
                                                   region=region,
                                                   zone=zone)

    def accelerators_to_hourly_cost(
        self,
        accelerators: Dict[str, int],
        use_spot: bool = False,
        region: Optional[str] = None,
        zone: Optional[str] = None,
    ) -> float:
        del accelerators, use_spot, region, zone
        # An offer's hourly price is for the whole box (GPUs included); there
        # is no separate per-GPU line item to add.
        return 0.0

    def get_egress_cost(self, num_gigabytes: float) -> float:
        del num_gigabytes
        # The provider publishes no egress charges ("no egress or ingress
        # charges", billing docs); nothing to price here. Revisit if overage
        # billing ever appears.
        return 0.0

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        return None

    # -- identity / credentials -------------------------------------------

    @classmethod
    def get_user_identities(cls) -> Optional[List[List[str]]]:
        return None

    @classmethod
    def get_current_user_identity(cls) -> Optional[List[str]]:
        return None

    @classmethod
    def get_current_user_identity_str(cls) -> Optional[str]:
        return None

    def get_credential_file_mounts(self) -> Dict[str, str]:
        return {
            f'~/.quanta/{filename}': f'~/.quanta/{filename}'
            for filename in _CREDENTIAL_FILES
        }

    @classmethod
    def _check_compute_credentials(cls) -> Tuple[bool, Optional[str]]:
        """Verify we can talk to QuantaCloud (GET /account)."""
        # pylint: disable=import-outside-toplevel
        from sky.adaptors import quantacloud as api

        key = os.environ.get(api.API_KEY_ENV, '').strip()
        if not key:
            path = os.path.expanduser(api.API_KEY_FILE)
            if os.path.exists(path):
                with open(path, encoding='utf-8') as handle:
                    key = handle.read().strip()
        if not key:
            return False, (
                f'{api.API_KEY_ENV} is not set and {api.API_KEY_FILE} does '
                'not exist. Create a key at https://console.quantacloud.net '
                '(API Keys).')
        try:
            api.QuantacloudClient(key).get_account()
        except api.QuantacloudError as exc:
            return False, str(exc)
        return True, None

    @classmethod
    def check_credentials(
            cls, cloud_capability: clouds.CloudCapability
    ) -> Tuple[bool, Optional[str]]:
        """Check QuantaCloud credentials for the requested capability.

        MUST accept ``cloud_capability``: ``sky check`` calls this with the
        capability positionally, so a no-arg override raises TypeError, the
        cloud is reported DISABLED, and every launch fails with "Task
        requires quantacloud which is not enabled" — with nothing pointing
        at the real cause (the bug urun-sh/skypilot#4 fixed for Spheron).
        """
        if cloud_capability == clouds.CloudCapability.COMPUTE:
            return cls._check_compute_credentials()
        return False, f'QuantaCloud does not support {cloud_capability.value}.'

    # -- provisioning ------------------------------------------------------

    def make_deploy_resources_variables(
        self,
        resources: 'resources_lib.Resources',
        cluster_name: resources_utils.ClusterName,
        region: clouds.Region,
        zones: Optional[List[clouds.Zone]],
        num_nodes: int,
        dryrun: bool = False,
        volume_mounts: Optional[List[Any]] = None,
    ) -> Dict[str, Optional[str]]:
        del cluster_name, dryrun, volume_mounts, num_nodes
        assert zones is None, 'QuantaCloud does not support zones.'
        resources = resources.assert_launchable()
        acc_dict = self.get_accelerators_from_instance_type(
            resources.instance_type)
        custom_resources = resources_utils.make_ray_custom_resources_str(
            acc_dict)
        # InstanceType is the "<gpu-slug>:<count>" token — the provisioner
        # re-resolves the concrete (ephemeral) offer UUID from the live
        # list at launch; the token itself is the stable catalog identity.
        return {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'region': region.name,
            'use_spot': resources.use_spot,
        }

    def _get_feasible_launchable_resources(
        self, resources: 'resources_lib.Resources'
    ) -> resources_utils.FeasibleResources:
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            resources = resources.copy(accelerators=None)
            return resources_utils.FeasibleResources([resources], [], None)

        def _make(instance_list):
            resource_list = []
            for instance_type in instance_list:
                r = resources.copy(
                    cloud=Quantacloud(),
                    instance_type=instance_type,
                    accelerators=None,
                    cpus=None,
                    memory=None,
                )
                resource_list.append(r)
            return resource_list

        accelerators = resources.accelerators
        if accelerators is None:
            default_instance_type = Quantacloud.get_default_instance_type(
                cpus=resources.cpus,
                memory=resources.memory,
                region=resources.region,
                zone=resources.zone,
                use_spot=resources.use_spot,
                max_hourly_cost=resources.max_hourly_cost,
            )
            if default_instance_type is None:
                return resources_utils.FeasibleResources([], [], None)
            return resources_utils.FeasibleResources(
                _make([default_instance_type]), [], None)

        assert len(accelerators) == 1, resources
        acc, acc_count = list(accelerators.items())[0]
        (instance_list, fuzzy_candidate_list
        ) = quantacloud_catalog.get_instance_type_for_accelerator(
            acc,
            acc_count,
            use_spot=resources.use_spot,
            cpus=resources.cpus,
            memory=resources.memory,
            region=resources.region,
            zone=resources.zone,
        )
        if instance_list is None:
            return resources_utils.FeasibleResources([], fuzzy_candidate_list,
                                                     None)
        return resources_utils.FeasibleResources(_make(instance_list),
                                                 fuzzy_candidate_list, None)

    @classmethod
    def query_status(
        cls,
        name: str,
        tag_filters: Dict[str, str],
        region: Optional[str],
        zone: Optional[str],
        **kwargs,
    ) -> List[Any]:
        # STATUS_VERSION is SKYPILOT, so the provisioner's query_instances is
        # the authority and this path is not used.
        raise NotImplementedError(
            'QuantaCloud uses StatusVersion.SKYPILOT; status comes from '
            'sky.provision.quantacloud.query_instances.')

    @classmethod
    def get_image_size(cls, image_id: str, region: Optional[str]) -> float:
        del image_id, region
        # The OS image is provider-side and its size is not exposed; 0.0 lets
        # every image through (same policy as the Vast/Latitude clouds).
        return 0.0
