"""Service specification for SkyServe."""
import json
import os
import textwrap
from typing import Any, Dict, Optional

import yaml

from sky.serve import constants
from sky.utils import common_utils
from sky.utils import schemas
from sky.utils import ux_utils


class SkyServiceSpec:
    """SkyServe service specification."""

    def __init__(
        self,
        readiness_path: str,
        initial_delay_seconds: int,
        min_replicas: int,
        max_replicas: Optional[int] = None,
        target_qps_per_replica: Optional[float] = None,
        post_data: Optional[Dict[str, Any]] = None,
        dynamic_ondemand_fallback: Optional[bool] = None,
        base_ondemand_fallback_replicas: Optional[int] = None,
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
        if max_replicas is not None and max_replicas < min_replicas:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'max_replicas must be greater than or equal to min_replicas'
                )

        if target_qps_per_replica is not None and max_replicas is None:
            with ux_utils.print_exception_no_traceback():
                raise ValueError('max_replicas must be set where '
                                 'target_qps_per_replica is set.')

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

        self._readiness_path: str = readiness_path
        self._initial_delay_seconds: int = initial_delay_seconds
        self._min_replicas: int = min_replicas
        self._max_replicas: Optional[int] = max_replicas
        self._target_qps_per_replica: Optional[float] = target_qps_per_replica
        self._post_data: Optional[Dict[str, Any]] = post_data
        self._dynamic_ondemand_fallback: Optional[
            bool] = dynamic_ondemand_fallback
        self._base_ondemand_fallback_replicas: Optional[
            int] = base_ondemand_fallback_replicas
        # _spot_locations will be set by set_spot_locations.
        self._upscale_delay_seconds: Optional[int] = upscale_delay_seconds
        self._downscale_delay_seconds: Optional[int] = downscale_delay_seconds

        self._use_ondemand_fallback: bool = (
            self.dynamic_ondemand_fallback is not None and
            self.dynamic_ondemand_fallback) or (
                self.base_ondemand_fallback_replicas is not None and
                self.base_ondemand_fallback_replicas > 0)

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
            service_config[
                'base_ondemand_fallback_replicas'] = policy_section.get(
                    'base_ondemand_fallback_replicas', None)
            service_config['dynamic_ondemand_fallback'] = policy_section.get(
                'dynamic_ondemand_fallback', None)

        return SkyServiceSpec(**service_config)

    @staticmethod
    def from_yaml(yaml_path: str) -> 'SkyServiceSpec':
        with open(os.path.expanduser(yaml_path), 'r', encoding='utf-8') as f:
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
        add_if_not_none('replica_policy', 'dynamic_ondemand_fallback',
                        self.dynamic_ondemand_fallback)
        add_if_not_none('replica_policy', 'base_ondemand_fallback_replicas',
                        self.base_ondemand_fallback_replicas)
        add_if_not_none('replica_policy', 'upscale_delay_seconds',
                        self.upscale_delay_seconds)
        add_if_not_none('replica_policy', 'downscale_delay_seconds',
                        self.downscale_delay_seconds)
        return config

    def probe_str(self):
        if self.post_data is None:
            return f'GET {self.readiness_path}'
        return f'POST {self.readiness_path} {json.dumps(self.post_data)}'

    def spot_policy_str(self):
        policy_strs = []
        if (self.dynamic_ondemand_fallback is not None and
                self.dynamic_ondemand_fallback):
            policy_strs.append('Dynamic on-demand fallback')
            if self.base_ondemand_fallback_replicas is not None:
                policy_strs.append(
                    f'with {self.base_ondemand_fallback_replicas}'
                    'base on-demand replicas')
        else:
            if self.base_ondemand_fallback_replicas is not None:
                plural = (''
                          if self.base_ondemand_fallback_replicas == 1 else 's')
                policy_strs.append('Static spot mixture with '
                                   f'{self.base_ondemand_fallback_replicas} '
                                   f'base on-demand replica{plural}')
        return ' '.join(policy_strs) if policy_strs else 'No spot policy'

    def autoscaling_policy_str(self):
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
            Replica autoscaling policy:       {self.autoscaling_policy_str()}
            Spot Policy:                      {self.spot_policy_str()}
        """)

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
    def base_ondemand_fallback_replicas(self) -> Optional[int]:
        return self._base_ondemand_fallback_replicas

    @property
    def dynamic_ondemand_fallback(self) -> Optional[bool]:
        return self._dynamic_ondemand_fallback

    @property
    def upscale_delay_seconds(self) -> Optional[int]:
        return self._upscale_delay_seconds

    @property
    def downscale_delay_seconds(self) -> Optional[int]:
        return self._downscale_delay_seconds

    @property
    def use_ondemand_fallback(self) -> bool:
        return self._use_ondemand_fallback
