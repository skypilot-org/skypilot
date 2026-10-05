"""Spheron cloud for SkyPilot.

Registered out-of-tree via ``@registry.CLOUD_REGISTRY.register`` — SkyPilot
0.13.0 supports third-party clouds through that decorator plus
``sky.provision.register_provisioner``, so no fork is required. There is no
``sky.clouds`` entry-point group, so something must import this module once at
startup to trigger the decorator (the controller does).

Modelled on ``sky/clouds/shadeform.py``. Deliberate differences, each grounded
in the 2026-09-10 API snapshot rather than copied:

* **Spot is supported.** 44 of 312 offers carry ``spot_price`` /
  ``instanceType: SPOT``. Shadeform's class declares SPOT unsupported; ours
  does not.
* **STOP is supported on some offers only.** The API exposes ``supportsPause``
  and ``pauseUnsupportedReason`` per offer. Because SkyPilot declares
  capability at the *cloud* level, not per instance type, we declare STOP
  UNSUPPORTED. Claiming it cloud-wide would strand a caller on the ~half of
  offers that cannot pause, and a stop that silently becomes a terminate
  destroys the disk.
* **Docker/image selection is not exposed.** Offers carry a fixed
  ``os_options`` list; an arbitrary image cannot be requested.
"""

from __future__ import annotations

import typing
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from sky import clouds
from sky.utils import registry
from sky.utils import resources_utils

from sky.catalog import spheron_catalog

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib

# The credential the provisioner needs on the machine running SkyPilot.
_CREDENTIAL_FILES = ["api_key"]

# Prefer a CUDA image: our runtime images are CUDA-only, and an AlmaLinux/plain
# option would boot fine and fail at CUDA import roughly 90 minutes in.
PREFERRED_OS_SUBSTRINGS = ["cuda"]


@registry.CLOUD_REGISTRY.register
class Spheron(clouds.Cloud):
    """Spheron GPU cloud.

    Spheron brokers GPUs across several underlying suppliers (sesterce,
    massed-compute, data-crunch, and Spheron's own pools). An "instance type"
    here is one concrete offer, encoded as ``{provider}_{offerId}`` so the
    provisioner can recover both halves — the deploy API requires them
    together.
    """

    # `_REPR` IS THE CATALOG NAME, not a display string. `Cloud.validate_region_zone`
    # passes `clouds=cls._REPR.lower()`, which SkyPilot turns into
    # `sky.catalog.{name}_catalog`. Inheriting the ABC's placeholder gives
    # `<cloud>` and every launch dies with
    #     ValueError: Cannot find module "sky.catalog.<cloud>_catalog"
    # This is exactly the bug that killed EVERY Shadeform launch until
    # urun-sh/skypilot#1 added `Shadeform._REPR` (see the skypilot-controller
    # Dockerfile's fork-pin comment). It hides well: `__repr__` below makes
    # repr()/str() read "Spheron" everywhere a human looks, so the only way to
    # catch it is to assert on `_REPR` itself -- which test_repr_is_the_catalog_name does.
    _REPR = "Spheron"

    _MAX_CLUSTER_NAME_LEN_LIMIT = 120

    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.STOP: (
            "Stop is offer-dependent on Spheron (supportsPause varies per "
            "offer) and SkyPilot declares this per cloud, not per instance "
            "type. Declared unsupported so a stop never silently becomes a "
            "terminate, which destroys the disk."
        ),
        clouds.CloudImplementationFeatures.MULTI_NODE: "Multi-node clusters are not supported on Spheron.",
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER: "Offers ship fixed storage; disk tier is not selectable.",
        clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER: "Network tier is not selectable on Spheron.",
        clouds.CloudImplementationFeatures.STORAGE_MOUNTING: "Object storage mounting is not supported on Spheron.",
        clouds.CloudImplementationFeatures.HOST_CONTROLLERS: "Host controllers are not supported on Spheron.",
        clouds.CloudImplementationFeatures.HIGH_AVAILABILITY_CONTROLLERS: "High availability controllers are not supported on Spheron.",
        clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER: "Disk cloning is not supported on Spheron.",
        clouds.CloudImplementationFeatures.IMAGE_ID: (
            "Images come from the offer's own os_options list; an arbitrary "
            "image id cannot be requested."
        ),
        clouds.CloudImplementationFeatures.DOCKER_IMAGE: "Docker image selection is not exposed by the Spheron API.",
        clouds.CloudImplementationFeatures.CUSTOM_MULTI_NETWORK: "Custom multi-network is not supported on Spheron.",
    }

    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT
    OPEN_PORTS_VERSION = clouds.OpenPortsVersion.LAUNCH_ONLY

    @classmethod
    def _unsupported_features_for_resources(
        cls,
        resources: "resources_lib.Resources",
        region: Optional[str] = None,
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        del resources, region
        return cls._CLOUD_UNSUPPORTED_FEATURES

    @classmethod
    def _max_cluster_name_length(cls) -> Optional[int]:
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    def __repr__(self):
        return "Spheron"

    # -- catalog-backed lookups -------------------------------------------

    @classmethod
    def regions_with_offering(
        cls,
        instance_type: str,
        accelerators: Optional[Dict[str, int]],
        use_spot: bool,
        region: Optional[str],
        zone: Optional[str],
        resources: Optional["resources_lib.Resources"] = None,
    ) -> List[clouds.Region]:
        del accelerators, resources
        assert zone is None, "Spheron does not support zones."
        regions = spheron_catalog.get_region_zones_for_instance_type(
            instance_type, use_spot
        )
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
    ) -> Iterator[None]:
        del num_nodes
        regions = cls.regions_with_offering(
            instance_type, accelerators, use_spot, zone=None,
            region=region)
        for r in regions:
            assert r.zones is None, r
            yield r.zones

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls, instance_type: str
    ) -> Tuple[Optional[float], Optional[float]]:
        return spheron_catalog.get_vcpus_mem_from_instance_type(instance_type)

    @classmethod
    def get_accelerators_from_instance_type(
        cls, instance_type: str
    ) -> Optional[Dict[str, Union[int, float]]]:
        return spheron_catalog.get_accelerators_from_instance_type(instance_type)

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
        return spheron_catalog.get_default_instance_type(
            cpus=cpus,
            memory=memory,
            disk_tier=disk_tier,
            local_disk=local_disk,
            region=region,
            zone=zone,
            use_spot=use_spot,
            max_hourly_cost=max_hourly_cost,
        )

    def instance_type_exists(self, instance_type: str) -> bool:
        return spheron_catalog.instance_type_exists(instance_type)

    def instance_type_to_hourly_cost(
        self,
        instance_type: str,
        use_spot: bool = False,
        region: Optional[str] = None,
        zone: Optional[str] = None,
    ) -> float:
        return spheron_catalog.get_hourly_cost(
            instance_type, use_spot=use_spot, region=region, zone=zone
        )

    def accelerators_to_hourly_cost(
        self,
        accelerators: Dict[str, int],
        use_spot: bool = False,
        region: Optional[str] = None,
        zone: Optional[str] = None,
    ) -> float:
        del accelerators, use_spot, region, zone
        # Accelerator cost is already included in the offer's hourly price;
        # there is no separate per-GPU line item to add.
        return 0.0

    def get_egress_cost(self, num_gigabytes: float) -> float:
        del num_gigabytes
        # Spheron publishes no egress line item.
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
        return {f"~/.spheron/{f}": f"~/.spheron/{f}" for f in _CREDENTIAL_FILES}

    @classmethod
    def _check_compute_credentials(cls) -> Tuple[bool, Optional[str]]:
        """Verify we can talk to Spheron.

        Checks the balance as well as auth: a funded-zero account authenticates
        perfectly and then fails every deploy, and that failure otherwise
        surfaces as a capacity problem rather than a billing one.
        """
        # Imported here so importing the cloud class never requires the client.
        # pylint: disable=import-outside-toplevel
        import os

        from sky.adaptors import spheron as api

        key = os.environ.get("SPHERON_API_KEY", "").strip()
        if not key:
            path = os.path.expanduser("~/.spheron/api_key")
            if os.path.exists(path):
                with open(path, encoding="utf-8") as handle:
                    key = handle.read().strip()
        if not key:
            return False, (
                "SPHERON_API_KEY is not set and ~/.spheron/api_key does not exist."
            )
        try:
            client = api.SpheronClient(key)
            balance = client.current_team_balance()
        except api.SpheronError as exc:
            return False, str(exc)
        if balance <= 0:
            return False, (
                f"Spheron credentials are valid but the team "
                f"balance is {balance} USD; every deploy will fail "
                f"until the account is funded."
            )
        return True, None

    @classmethod
    def check_credentials(
        cls, cloud_capability: clouds.CloudCapability
    ) -> Tuple[bool, Optional[str]]:
        """Check Spheron credentials for the requested capability.

        MUST accept ``cloud_capability``: ``sky check`` calls this with the
        capability positionally, so a no-arg override raises TypeError, the
        cloud is reported DISABLED, and every launch fails with "Task requires
        spheron which is not enabled" -- with nothing pointing at the real
        cause. Storage is unsupported (see _CLOUD_UNSUPPORTED_FEATURES), so
        only COMPUTE can be satisfied.
        """
        if cloud_capability == clouds.CloudCapability.COMPUTE:
            return cls._check_compute_credentials()
        return False, (
            f"Spheron does not support {cloud_capability.value}."
        )

    # -- provisioning ------------------------------------------------------

    def make_deploy_resources_variables(
        self,
        resources: "resources_lib.Resources",
        cluster_name: resources_utils.ClusterName,
        region: clouds.Region,
        zones: Optional[List[clouds.Zone]],
        num_nodes: int,
        dryrun: bool = False,
        volume_mounts: Optional[List[Any]] = None,
    ) -> Dict[str, Optional[str]]:
        del cluster_name, dryrun, volume_mounts, num_nodes
        assert zones is None, "Spheron does not support zones."
        resources = resources.assert_launchable()
        acc_dict = self.get_accelerators_from_instance_type(resources.instance_type)
        custom_resources = resources_utils.make_ray_custom_resources_str(acc_dict)
        # InstanceType is "{provider}_{offerId}" -- the provisioner needs both
        # halves, because the deploy API requires them together.
        provider, _, offer_id = (resources.instance_type or "").partition("_")
        return {
            "instance_type": resources.instance_type,
            "custom_resources": custom_resources,
            "region": region.name,
            "use_spot": resources.use_spot,
            "spheron_provider": provider,
            "spheron_offer_id": offer_id,
        }

    def _get_feasible_launchable_resources(
        self, resources: "resources_lib.Resources"
    ) -> resources_utils.FeasibleResources:
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            return resources_utils.FeasibleResources([resources], [], None)

        def _make(instance_list):
            resource_list = []
            for instance_type in instance_list:
                r = resources.copy(
                    cloud=Spheron(),
                    instance_type=instance_type,
                    accelerators=None,
                    cpus=None,
                    memory=None,
                )
                resource_list.append(r)
            return resource_list

        accelerators = resources.accelerators
        if accelerators is None:
            default_instance_type = Spheron.get_default_instance_type(
                cpus=resources.cpus,
                memory=resources.memory,
                disk_tier=resources.disk_tier,
                region=resources.region,
                zone=resources.zone,
                use_spot=resources.use_spot,
            )
            if default_instance_type is None:
                return resources_utils.FeasibleResources([], [], None)
            return resources_utils.FeasibleResources(
                _make([default_instance_type]), [], None
            )

        assert len(accelerators) == 1, resources
        acc, acc_count = list(accelerators.items())[0]
        (instance_list, fuzzy_candidate_list) = (
            spheron_catalog.get_instance_type_for_accelerator(
                acc,
                acc_count,
                use_spot=resources.use_spot,
                cpus=resources.cpus,
                memory=resources.memory,
                region=resources.region,
                zone=resources.zone,
            )
        )
        if instance_list is None:
            return resources_utils.FeasibleResources([], fuzzy_candidate_list, None)
        return resources_utils.FeasibleResources(
            _make(instance_list), fuzzy_candidate_list, None
        )

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
            "Spheron uses StatusVersion.SKYPILOT; status comes from "
            "sky.provision.spheron.query_instances."
        )
