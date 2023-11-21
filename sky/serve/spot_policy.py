""" Sky Spot Policy for SkyServe."""
import enum
import logging
import random
import typing
from typing import Any, Dict, List, Optional, Tuple, Type

from sky.serve import serve_state

if typing.TYPE_CHECKING:
    from sky.serve import replica_managers
    from sky.serve import service_spec

logger = logging.getLogger(__name__)

# TODO(tian): Move this to user config.
_DEFAULT_OVER_PROVISION_NUM = 1


class SpotPlacer:
    """Spot Placement specification."""
    NAME: Optional[str] = None
    REGISTRY: Dict[str, Type['SpotPlacer']] = dict()

    def __init__(self, spec: 'service_spec.SkyServiceSpec') -> None:
        assert spec.spot_zones is not None
        self.zones = list(spec.spot_zones)

    def __init_subclass__(cls) -> None:
        if cls.NAME is None:
            # This is an abstract class, don't put it in the registry.
            return
        assert cls.NAME not in cls.REGISTRY, f'Name {cls.NAME} already exists'
        cls.REGISTRY[cls.NAME] = cls

    def select(self) -> str:
        """Select next zone to place spot instance."""
        raise NotImplementedError

    def handle_preemption(self, zone: str) -> None:
        """Handle preemption of spot instance in given zone."""
        del zone  # Unused.

    def __repr__(self) -> str:
        return f'{self.NAME}SpotPlacer()'

    @classmethod
    def from_spec(cls, spec: 'service_spec.SkyServiceSpec') -> 'SpotPlacer':
        assert spec.spot_placer is not None
        return cls.REGISTRY[spec.spot_placer](spec)


class EvenSpreadSpotPlacer(SpotPlacer):
    """Evenly spread spot instances across zones."""
    NAME: Optional[str] = 'EvenSpread'

    def __init__(self, spec: 'service_spec.SkyServiceSpec') -> None:
        super().__init__(spec)
        self.current_zone_idx: int = 0

    def select(self) -> str:
        zone = self.zones[self.current_zone_idx % len(self.zones)]
        self.current_zone_idx += 1
        return zone


class SpotZoneType(enum.Enum):
    """Spot Zone Type."""
    ACTIVE = 'ACTIVE'
    PREEMPTED = 'PREEMPTED'


class HistoricalSpotPlacer(SpotPlacer):
    """SpotPlacer with historical information."""
    NAME: Optional[str] = None

    def __init__(self, spec: 'service_spec.SkyServiceSpec') -> None:
        super().__init__(spec)
        self.zone2type: Dict[str, SpotZoneType] = {
            zone: SpotZoneType.ACTIVE for zone in self.zones
        }

    def move_zone_to_active(self, zone: str) -> None:
        assert zone in self.zone2type
        self.zone2type[zone] = SpotZoneType.ACTIVE

    def move_zone_to_preempted(self, zone: str) -> None:
        assert zone in self.zone2type
        self.zone2type[zone] = SpotZoneType.PREEMPTED

    def handle_preemption(self, zone) -> None:
        self.move_zone_to_preempted(zone)

    def clear_preempted_zones(self) -> None:
        for zone in self.zones:
            self.move_zone_to_active(zone)

    def active_zones(self) -> List[str]:
        return [
            zone for zone, zone_type in self.zone2type.items()
            if zone_type == SpotZoneType.ACTIVE
        ]

    def preempted_zones(self) -> List[str]:
        return [
            zone for zone, zone_type in self.zone2type.items()
            if zone_type == SpotZoneType.PREEMPTED
        ]


class EagerFailoverSpotPlacer(HistoricalSpotPlacer):
    """Eagerly failover to a different zone when preempted."""
    NAME: Optional[str] = 'EagerFailover'

    def select(self) -> str:
        zone = random.choice(self.zones)
        while zone in self.preempted_zones():
            zone = random.choice(self.zones)
        self.clear_preempted_zones()
        return zone


class DynamicFailoverSpotPlacer(HistoricalSpotPlacer):
    """Dynamic failover to an active zone when preempted."""
    NAME: Optional[str] = 'DynamicFailover'

    def select(self) -> str:
        if len(self.active_zones()) > 0:
            zone = random.choice(self.active_zones())
        else:
            zone = random.choice(self.preempted_zones())
            # TODO(tian): Why move to active?
            self.move_zone_to_active(zone)
        return zone


class SpotMixer:
    """Spot OnDemand mixture specification."""
    NAME: Optional[str] = None
    REGISTRY: Dict[str, Type['SpotMixer']] = dict()

    def __init__(self, spec: 'service_spec.SkyServiceSpec') -> None:
        pass

    def __init_subclass__(cls) -> None:
        if cls.NAME is None:
            # This is an abstract class, don't put it in the registry.
            return
        assert cls.NAME not in cls.REGISTRY, f'Name {cls.NAME} already exists'
        cls.REGISTRY[cls.NAME] = cls

    def generate_mix_plan(
            self, num_target: int,
            replica_infos: List['replica_managers.ReplicaInfo']
    ) -> Tuple[int, int]:
        """Generate mix plan for spot and on-demand instances.

        Args:
            num_target (int): target number of replicas.

        Returns:
            Tuple[int, int]: (num_on_demand_delta, num_spot_delta).
        """
        raise NotImplementedError

    def __repr__(self) -> str:
        return f'{self.NAME}SpotMixer()'

    @classmethod
    def from_spec(cls, spec: 'service_spec.SkyServiceSpec') -> 'SpotMixer':
        assert spec.spot_mixer is not None
        return cls.REGISTRY[spec.spot_mixer](spec)


class OnDemandFallbackSpotMixer(SpotMixer):
    """Fallback to on-demand when spot cannot reach target."""
    NAME: Optional[str] = 'OnDemandFallback'

    def generate_mix_plan(
            self, num_target: int,
            replica_infos: List['replica_managers.ReplicaInfo']
    ) -> Tuple[int, int]:
        # TODO(tian): Considering not alive replicas.
        alive_replica_infos = [info for info in replica_infos if info.is_alive]
        current_num_on_demand = 0
        current_num_alive_spot = 0
        current_num_ready_spot = 0
        for info in alive_replica_infos:
            if info.is_spot:
                if info.status == serve_state.ReplicaStatus.READY:
                    current_num_ready_spot += 1
                current_num_alive_spot += 1
            else:
                current_num_on_demand += 1
        num_to_provision = num_target + _DEFAULT_OVER_PROVISION_NUM
        spot_gap = num_to_provision - current_num_alive_spot
        if spot_gap > 0:
            # If there are not enough spot instances, this is the
            # first time we launch on-demand and spot simultaneously.
            # There shouldn't be any on-demand instances.
            assert current_num_on_demand == 0
            # Launch spot_gap spot and on-demand simultaneously.
            num_spot_delta = spot_gap
            num_on_demand_delta = spot_gap - current_num_on_demand
        else:
            num_on_demand_delta = 0
            if current_num_ready_spot >= num_to_provision:
                # Clean all on-demand instances if enough spot instances
                # are ready.
                num_on_demand_delta = -current_num_on_demand
            # Cleanup redundant spot instances.
            num_spot_delta = spot_gap
        return num_on_demand_delta, num_spot_delta


class SpotPolicy:
    """Spot Policy specification."""

    def __init__(self, spec: 'service_spec.SkyServiceSpec') -> None:
        self.spot_placer = SpotPlacer.from_spec(spec)
        self.spot_mixer = SpotMixer.from_spec(spec)

    def _get_next_zone(self) -> str:
        zone = self.spot_placer.select()
        logger.info(f'Chosen zone {zone} with {self.spot_placer}')
        return zone

    def _generate_mix_plan(
            self, num_target: int,
            replica_infos: List['replica_managers.ReplicaInfo']
    ) -> Tuple[int, int]:
        num_on_demand_delta, num_spot_delta = self.spot_mixer.generate_mix_plan(
            num_target, replica_infos)
        logger.info(f'Generated mix plan: {num_on_demand_delta} '
                    f'on-demand delta, {num_spot_delta} spot delta '
                    f'with {self.spot_mixer}')
        return num_on_demand_delta, num_spot_delta

    def get_spot_resources_override_dict(self) -> Dict[str, Any]:
        return {'use_spot': True, 'spot_recovery': None}

    def get_on_demand_resources_override_dict(self) -> Dict[str, Any]:
        return {'use_spot': False, 'spot_recovery': None}

    def generate_autoscaling_plan(
        self, num_target: int,
        replica_infos: List['replica_managers.ReplicaInfo']
    ) -> Tuple[int, List[str], List[int]]:
        """Generate autoscaling plan.

        Returns:
            int: Number of OnDemand instances to launch.
            List[str]: Zones of spot instances to launch. Each zone represent
                one spot instance to launch.
            List[int]: Replica IDs to scale down.
        """
        num_on_demand_delta, num_spot_delta = self._generate_mix_plan(
            num_target, replica_infos)
        replica_ids_to_scale_down = []
        alive_replica_infos = [info for info in replica_infos if info.is_alive]
        if num_on_demand_delta < 0:
            for info in alive_replica_infos:
                if not info.is_spot:
                    replica_ids_to_scale_down.append(info.replica_id)
                    num_on_demand_delta += 1
                    if num_on_demand_delta == 0:
                        break
        spot_zones_to_launch = []
        if num_spot_delta < 0:
            for info in alive_replica_infos:
                if info.is_spot:
                    replica_ids_to_scale_down.append(info.replica_id)
                    num_spot_delta += 1
                    if num_spot_delta == 0:
                        break
        else:
            if isinstance(self.spot_placer, HistoricalSpotPlacer):
                logger.info('Active/Preempted zones list: '
                            f'{self.spot_placer.zone2type}')
            for _ in range(num_spot_delta):
                spot_zones_to_launch.append(self._get_next_zone())
        logger.info(f'Autoscaling plan: Add {num_on_demand_delta} '
                    f'on-demand instances, add {len(spot_zones_to_launch)} '
                    f'spot instances in {spot_zones_to_launch}, scale down '
                    f'{len(replica_ids_to_scale_down)} replicas with id '
                    f'{replica_ids_to_scale_down}')
        return (num_on_demand_delta, spot_zones_to_launch,
                replica_ids_to_scale_down)

    def handle_preemption(self, zone):
        self.spot_placer.handle_preemption(zone)
        logger.info(f'Handle preemption in {zone}')
