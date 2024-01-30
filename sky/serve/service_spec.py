"""Service specification for SkyServe."""
import json
import os
import textwrap
from typing import Any, Dict, List, Optional

import yaml

from sky.serve import autoscalers
from sky.serve import constants
from sky.serve import spot_policies
from sky.utils import common_utils
from sky.utils import schemas
from sky.utils import ux_utils

_policy_to_autoscaler_and_spot_placer = {
    'SpotHedge': ('SPOT_ON_DEMAND_REQUEST_RATE_AUTOSCALER', 'DYNAMIC_FAILOVER'),
    'SpotOnly': ('SPOT_REQUEST_RATE_AUTOSCALER', 'DYNAMIC_FAILOVER'),
}


class SkyServiceSpec:
    """SkyServe service specification."""

    def __init__(
        self,
        readiness_path: str,
        initial_delay_seconds: int,
        min_replicas: int,
        max_replicas: Optional[int] = None,
        extra_on_demand_replicas: Optional[int] = None,
        target_qps_per_replica: Optional[float] = None,
        post_data: Optional[Dict[str, Any]] = None,
        spot_placer: Optional[str] = None,
        spot_policy: Optional[str] = None,
        autoscaler: Optional[str] = None,
        num_overprovision: Optional[int] = None,
        init_replicas: Optional[int] = None,
        upscale_delay_seconds: Optional[int] = None,
        downscale_delay_seconds: Optional[int] = None,
        # The following arguments are deprecated.
        # TODO(ziming): remove this after 2 minor release, i.e. 0.6.0.
        # Deprecated: Always be True
        auto_restart: Optional[bool] = None,
        # Deprecated: replaced by the target_qps_per_replica.
        qps_upper_threshold: Optional[float] = None,
        qps_lower_threshold: Optional[float] = None,
    ) -> None:
        if min_replicas < 0:
            with ux_utils.print_exception_no_traceback():
                raise ValueError('min_replicas must be greater or equal to 0')
        if max_replicas is not None and max_replicas < min_replicas:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'max_replicas must be greater than or equal to min_replicas'
                )
        if target_qps_per_replica is not None and target_qps_per_replica <= 0:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'target_qps_per_replica must be greater than 0')
        if not readiness_path.startswith('/'):
            with ux_utils.print_exception_no_traceback():
                raise ValueError('readiness_path must start with a slash (/). '
                                 f'Got: {readiness_path}')

        if qps_upper_threshold is not None or qps_lower_threshold is not None:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'Field `qps_upper_threshold` and `qps_lower_threshold`'
                    'under `replica_policy` are deprecated. '
                    'Please use target_qps_per_replica instead.')

        if auto_restart is not None:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'Field `auto_restart` under `replica_policy` is deprecated.'
                    'Currently, SkyServe will cleanup failed replicas'
                    'and auto restart it to keep the service running.')

        if spot_policy is not None:
            if autoscaler is not None or spot_placer is not None:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        'Cannot specify `autoscaler` and `spot_placer`'
                        'when `spot_policy` is specified.')
            # TODO(MaoZiming): do not hardcode the name
            if spot_policy in _policy_to_autoscaler_and_spot_placer:
                autoscaler, spot_placer = _policy_to_autoscaler_and_spot_placer[
                    spot_policy]
            else:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(f'Unsupported spot policy: {spot_policy}.')

        if autoscaler is None:
            autoscaler = autoscalers.DEFAULT_AUTOSCALER

        if autoscaler not in autoscalers.Autoscaler.get_autoscaler_names():
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Unsupported autoscaler: {autoscaler}.')

        if spot_placer is None:
            spot_placer = spot_policies.DEFAULT_SPOT_POLICY

        if (spot_placer is not None and
                spot_placer not in spot_policies.SpotPlacer.get_policy_names()):
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Unsupported spot placer: {spot_placer}.')

        self._readiness_path: str = readiness_path
        self._initial_delay_seconds: int = initial_delay_seconds
        self._min_replicas: int = min_replicas
        self._max_replicas: Optional[int] = max_replicas
        self._target_qps_per_replica: Optional[float] = target_qps_per_replica
        self._post_data: Optional[Dict[str, Any]] = post_data
        self._spot_placer: Optional[str] = spot_placer
        self._autoscaler: str = autoscaler
        self._num_overprovision: Optional[int] = num_overprovision
        self._init_replicas: Optional[int] = init_replicas
        self._extra_on_demand_replicas: Optional[int] = extra_on_demand_replicas
        # _spot_zones will be set by set_spot_zones from resource config.
        self._spot_zones: Optional[List[str]] = None
        self._upscale_delay_seconds: int = (
            upscale_delay_seconds if upscale_delay_seconds is not None else
            constants.AUTOSCALER_DEFAULT_UPSCALE_DELAY_SECONDS)
        self._downscale_delay_seconds: int = (
            downscale_delay_seconds if downscale_delay_seconds is not None else
            constants.AUTOSCALER_DEFAULT_DOWNSCALE_DELAY_SECONDS)

    @staticmethod
    def from_yaml_config(config: Dict[str, Any]) -> 'SkyServiceSpec':
        common_utils.validate_schema(config, schemas.get_service_schema(),
                                     'Invalid service YAML: ')
        if 'replicas' in config and 'replica_policy' in config:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'Cannot specify both `replicas` and `replica_policy` in '
                    'the service YAML. Please use one of them.')

        service_config: Dict[str, Any] = {}

        readiness_section = config['readiness_probe']
        if isinstance(readiness_section, str):
            service_config['readiness_path'] = readiness_section
            initial_delay_seconds = None
            post_data = None
        else:
            service_config['readiness_path'] = readiness_section['path']
            initial_delay_seconds = readiness_section.get(
                'initial_delay_seconds', None)
            post_data = readiness_section.get('post_data', None)
        if initial_delay_seconds is None:
            initial_delay_seconds = constants.DEFAULT_INITIAL_DELAY_SECONDS
        service_config['initial_delay_seconds'] = initial_delay_seconds
        if isinstance(post_data, str):
            try:
                post_data = json.loads(post_data)
            except json.JSONDecodeError as e:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        'Invalid JSON string for `post_data` in the '
                        '`readiness_probe` section of your service YAML.'
                    ) from e
        service_config['post_data'] = post_data

        policy_section = config.get('replica_policy', None)
        simplified_policy_section = config.get('replicas', None)
        if policy_section is None or simplified_policy_section is not None:
            if simplified_policy_section is not None:
                min_replicas = simplified_policy_section
            else:
                min_replicas = constants.DEFAULT_MIN_REPLICAS
            service_config['min_replicas'] = min_replicas
            service_config['max_replicas'] = None
            service_config['target_qps_per_replica'] = None
            service_config['upscale_delay_seconds'] = None
            service_config['downscale_delay_seconds'] = None
        else:
            service_config['min_replicas'] = policy_section['min_replicas']
            service_config['max_replicas'] = policy_section.get(
                'max_replicas', None)
            service_config['qps_upper_threshold'] = policy_section.get(
                'qps_upper_threshold', None)
            service_config['qps_lower_threshold'] = policy_section.get(
                'qps_lower_threshold', None)
            service_config['target_qps_per_replica'] = policy_section.get(
                'target_qps_per_replica', None)
            service_config['auto_restart'] = policy_section.get(
                'auto_restart', None)
            service_config['upscale_delay_seconds'] = policy_section.get(
                'upscale_delay_seconds', None)
            service_config['downscale_delay_seconds'] = policy_section.get(
                'downscale_delay_seconds', None)

            service_config['spot_placer'] = policy_section.get(
                'spot_placer', None)
            service_config['autoscaler'] = policy_section.get(
                'autoscaler', None)
            service_config['num_overprovision'] = policy_section.get(
                'num_overprovision', None)
            service_config['init_replicas'] = policy_section.get(
                'init_replicas', None)
            service_config['extra_on_demand_replicas'] = policy_section.get(
                'extra_on_demand_replicas', None)
            service_config['spot_policy'] = policy_section.get(
                'spot_policy', None)

        return SkyServiceSpec(**service_config)

    @staticmethod
    def from_yaml(yaml_path: str) -> 'SkyServiceSpec':
        with open(os.path.expanduser(yaml_path), 'r') as f:
            config = yaml.safe_load(f)

        if isinstance(config, str):
            with ux_utils.print_exception_no_traceback():
                raise ValueError('YAML loaded as str, not as dict. '
                                 f'Is it correct? Path: {yaml_path}')

        if config is None:
            config = {}

        if 'service' not in config:
            with ux_utils.print_exception_no_traceback():
                raise ValueError('Service YAML must have a "service" section. '
                                 f'Is it correct? Path: {yaml_path}')

        return SkyServiceSpec.from_yaml_config(config['service'])

    def to_yaml_config(self) -> Dict[str, Any]:
        config = dict()

        def add_if_not_none(section, key, value, no_empty: bool = False):
            if no_empty and not value:
                return
            if value is not None:
                if key is None:
                    config[section] = value
                else:
                    if section not in config:
                        config[section] = dict()
                    config[section][key] = value

        add_if_not_none('readiness_probe', 'path', self.readiness_path)
        add_if_not_none('readiness_probe', 'initial_delay_seconds',
                        self.initial_delay_seconds)
        add_if_not_none('readiness_probe', 'post_data', self.post_data)
        add_if_not_none('replica_policy', 'min_replicas', self.min_replicas)
        add_if_not_none('replica_policy', 'max_replicas', self.max_replicas)
        add_if_not_none('replica_policy', 'target_qps_per_replica',
                        self.target_qps_per_replica)
        add_if_not_none('replica_policy', 'spot_placer', self._spot_placer)
        add_if_not_none('replica_policy', 'autoscaler', self._autoscaler)
        add_if_not_none('replica_policy', 'num_overprovision',
                        self._num_overprovision)
        add_if_not_none('replica_policy', 'init_replicas', self._init_replicas)
        add_if_not_none('replica_policy', 'extra_on_demand_replicas',
                        self._extra_on_demand_replicas)
        add_if_not_none('replica_policy', 'upscale_delay_seconds',
                        self._upscale_delay_seconds)
        add_if_not_none('replica_policy', 'downscale_delay_seconds',
                        self._downscale_delay_seconds)
        return config

    def probe_str(self):
        if self.post_data is None:
            return f'GET {self.readiness_path}'
        return f'POST {self.readiness_path} {json.dumps(self.post_data)}'

    def spot_policy_str(self):
        policy = ''
        if self.spot_placer:
            policy += self.spot_placer
        else:
            return 'No spot policy'
        if self.autoscaler:
            policy += f' with {self.autoscaler}'
        if self.num_overprovision is not None and self.num_overprovision > 0:
            policy += f' with {self.num_overprovision} extra spot instance(s)'
        return policy if policy else 'No spot policy'

    def policy_str(self):
        # TODO(MaoZiming): Update policy_str
        min_plural = '' if self.min_replicas == 1 else 's'
        if self.max_replicas == self.min_replicas or self.max_replicas is None:
            return f'Fixed {self.min_replicas} replica{min_plural}'
        # TODO(tian): Refactor to contain more information
        max_plural = '' if self.max_replicas == 1 else 's'
        return (f'Autoscaling from {self.min_replicas} to '
                f'{self.max_replicas} replica{max_plural}')

    def __repr__(self) -> str:
        return textwrap.dedent(f"""\
            Readiness probe method:           {self.probe_str()}
            Readiness initial delay seconds:  {self.initial_delay_seconds}
            Replica autoscaling policy:       {self.policy_str()}
            Spot Policy:                      {self.spot_policy_str()}\
        """)

    def set_spot_zones(self, zones: List[str]) -> None:
        self._spot_zones = zones

    @property
    def readiness_path(self) -> str:
        return self._readiness_path

    @property
    def initial_delay_seconds(self) -> int:
        return self._initial_delay_seconds

    @property
    def min_replicas(self) -> int:
        return self._min_replicas

    @property
    def max_replicas(self) -> Optional[int]:
        # If None, treated as having the same value of min_replicas.
        return self._max_replicas

    @property
    def target_qps_per_replica(self) -> Optional[float]:
        return self._target_qps_per_replica

    @property
    def post_data(self) -> Optional[Dict[str, Any]]:
        return self._post_data

    @property
    def spot_placer(self) -> Optional[str]:
        return self._spot_placer

    @property
    def autoscaler(self) -> Optional[str]:
        return self._autoscaler

    @property
    def spot_zones(self) -> Optional[List[str]]:
        return self._spot_zones

    @property
    def num_overprovision(self) -> Optional[int]:
        return self._num_overprovision

    @property
    def init_replicas(self) -> Optional[int]:
        return self._init_replicas

    @property
    def extra_on_demand_replicas(self) -> Optional[int]:
        return self._extra_on_demand_replicas

    @property
    def upscale_delay_seconds(self) -> int:
        return self._upscale_delay_seconds

    @property
    def downscale_delay_seconds(self) -> Optional[int]:
        return self._downscale_delay_seconds
