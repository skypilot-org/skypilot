"""Service specification for SkyServe."""
import json
import os
import textwrap
import typing
from typing import Any, Dict, List, Optional

from sky import serve
from sky.adaptors import common as adaptors_common
from sky.serve import constants
from sky.serve import load_balancing_policies as lb_policies
from sky.serve import serve_utils
from sky.serve import spot_placer as spot_placer_lib
from sky.utils import common_utils
from sky.utils import schemas
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import yaml
else:
    yaml = adaptors_common.LazyImport('yaml')


class SkyServiceSpec:
    """SkyServe service specification."""

    def __init__(
        self,
        readiness_path: str,
        initial_delay_seconds: int,
        readiness_timeout_seconds: int,
        min_replicas: int,
        max_replicas: Optional[int] = None,
        num_overprovision: Optional[int] = None,
        ports: Optional[str] = None,
        target_qps_per_replica: Optional[float] = None,
        post_data: Optional[Dict[str, Any]] = None,
        tls_credential: Optional[serve_utils.TLSCredential] = None,
        readiness_headers: Optional[Dict[str, str]] = None,
        dynamic_ondemand_fallback: Optional[bool] = None,
        base_ondemand_fallback_replicas: Optional[int] = None,
        spot_placer: Optional[str] = None,
        upscale_delay_seconds: Optional[int] = None,
        downscale_delay_seconds: Optional[int] = None,
        load_balancing_policy: Optional[str] = None,
    ) -> None:
        if max_replicas is not None and max_replicas < min_replicas:
            with ux_utils.print_exception_no_traceback():
                raise ValueError('max_replicas must be greater than or '
                                 'equal to min_replicas. Found: '
                                 f'min_replicas={min_replicas}, '
                                 f'max_replicas={max_replicas}')

        if target_qps_per_replica is not None:
            if max_replicas is None:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError('max_replicas must be set where '
                                     'target_qps_per_replica is set.')
        else:
            if max_replicas is not None and max_replicas != min_replicas:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        'Detected different min_replicas and max_replicas '
                        'while target_qps_per_replica is not set. To enable '
                        'autoscaling, please set target_qps_per_replica.')

        if not readiness_path.startswith('/'):
            with ux_utils.print_exception_no_traceback():
                raise ValueError('readiness_path must start with a slash (/). '
                                 f'Got: {readiness_path}')

        # Add the check for unknown load balancing policies
        if (load_balancing_policy is not None and
                load_balancing_policy not in serve.LB_POLICIES):
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Unknown load balancing policy: {load_balancing_policy}. '
                    f'Available policies: {list(serve.LB_POLICIES.keys())}')
        self._readiness_path: str = readiness_path
        self._initial_delay_seconds: int = initial_delay_seconds
        self._readiness_timeout_seconds: int = readiness_timeout_seconds
        self._min_replicas: int = min_replicas
        self._max_replicas: Optional[int] = max_replicas
        self._num_overprovision: Optional[int] = num_overprovision
        self._ports: Optional[str] = ports
        self._target_qps_per_replica: Optional[float] = target_qps_per_replica
        self._post_data: Optional[Dict[str, Any]] = post_data
        self._tls_credential: Optional[serve_utils.TLSCredential] = (
            tls_credential)
        self._readiness_headers: Optional[Dict[str, str]] = readiness_headers
        self._dynamic_ondemand_fallback: Optional[
            bool] = dynamic_ondemand_fallback
        self._base_ondemand_fallback_replicas: Optional[
            int] = base_ondemand_fallback_replicas
        self._spot_placer: Optional[str] = spot_placer
        self._upscale_delay_seconds: Optional[int] = upscale_delay_seconds
        self._downscale_delay_seconds: Optional[int] = downscale_delay_seconds
        self._load_balancing_policy: Optional[str] = load_balancing_policy

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
            readiness_timeout_seconds = None
            readiness_headers = None
        else:
            service_config['readiness_path'] = readiness_section['path']
            initial_delay_seconds = readiness_section.get(
                'initial_delay_seconds', None)
            post_data = readiness_section.get('post_data', None)
            readiness_timeout_seconds = readiness_section.get(
                'timeout_seconds', None)
            readiness_headers = readiness_section.get('headers', None)
        if initial_delay_seconds is None:
            initial_delay_seconds = constants.DEFAULT_INITIAL_DELAY_SECONDS
        service_config['initial_delay_seconds'] = initial_delay_seconds
        if readiness_timeout_seconds is None:
            readiness_timeout_seconds = (
                constants.DEFAULT_READINESS_PROBE_TIMEOUT_SECONDS)
        service_config['readiness_timeout_seconds'] = readiness_timeout_seconds
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
        service_config['readiness_headers'] = readiness_headers

        ports = config.get('ports', None)
        if ports is not None:
            assert isinstance(ports, int)
            if not 1 <= ports <= 65535:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError('Port must be between 1 and 65535.')
        service_config['ports'] = str(ports) if ports is not None else None

        policy_section = config.get('replica_policy', None)
        simplified_policy_section = config.get('replicas', None)
        if policy_section is None or simplified_policy_section is not None:
            if simplified_policy_section is not None:
                min_replicas = simplified_policy_section
            else:
                min_replicas = constants.DEFAULT_MIN_REPLICAS
            service_config['min_replicas'] = min_replicas
            service_config['max_replicas'] = None
            service_config['num_overprovision'] = None
            service_config['target_qps_per_replica'] = None
            service_config['upscale_delay_seconds'] = None
            service_config['downscale_delay_seconds'] = None
        else:
            service_config['min_replicas'] = policy_section['min_replicas']
            service_config['max_replicas'] = policy_section.get(
                'max_replicas', None)
            service_config['num_overprovision'] = policy_section.get(
                'num_overprovision', None)
            service_config['target_qps_per_replica'] = policy_section.get(
                'target_qps_per_replica', None)
            service_config['upscale_delay_seconds'] = policy_section.get(
                'upscale_delay_seconds', None)
            service_config['downscale_delay_seconds'] = policy_section.get(
                'downscale_delay_seconds', None)
            service_config[
                'base_ondemand_fallback_replicas'] = policy_section.get(
                    'base_ondemand_fallback_replicas', None)
            service_config['dynamic_ondemand_fallback'] = policy_section.get(
                'dynamic_ondemand_fallback', None)
            service_config['spot_placer'] = policy_section.get(
                'spot_placer', None)

        service_config['load_balancing_policy'] = config.get(
            'load_balancing_policy', None)

        tls_section = config.get('tls', None)
        if tls_section is not None:
            service_config['tls_credential'] = serve_utils.TLSCredential(
                keyfile=tls_section.get('keyfile', None),
                certfile=tls_section.get('certfile', None),
            )

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
        config: Dict[str, Any] = {}

        def add_if_not_none(section: str,
                            key: Optional[str],
                            value: Any,
                            no_empty: bool = False):
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
        add_if_not_none('readiness_probe', 'timeout_seconds',
                        self.readiness_timeout_seconds)
        add_if_not_none('readiness_probe', 'headers', self._readiness_headers)
        add_if_not_none('replica_policy', 'min_replicas', self.min_replicas)
        add_if_not_none('replica_policy', 'max_replicas', self.max_replicas)
        add_if_not_none('replica_policy', 'num_overprovision',
                        self.num_overprovision)
        add_if_not_none('replica_policy', 'target_qps_per_replica',
                        self.target_qps_per_replica)
        add_if_not_none('replica_policy', 'dynamic_ondemand_fallback',
                        self.dynamic_ondemand_fallback)
        add_if_not_none('replica_policy', 'base_ondemand_fallback_replicas',
                        self.base_ondemand_fallback_replicas)
        add_if_not_none('replica_policy', 'spot_placer', self.spot_placer)
        add_if_not_none('replica_policy', 'upscale_delay_seconds',
                        self.upscale_delay_seconds)
        add_if_not_none('replica_policy', 'downscale_delay_seconds',
                        self.downscale_delay_seconds)
        add_if_not_none('load_balancing_policy', None,
                        self._load_balancing_policy)
        add_if_not_none('ports', None, int(self.ports) if self.ports else None)
        if self.tls_credential is not None:
            add_if_not_none('tls', 'keyfile', self.tls_credential.keyfile)
            add_if_not_none('tls', 'certfile', self.tls_credential.certfile)
        return config

    def probe_str(self):
        if self.post_data is None:
            method = f'GET {self.readiness_path}'
        else:
            method = f'POST {self.readiness_path} {json.dumps(self.post_data)}'
        headers = ('' if self.readiness_headers is None else
                   ' with custom headers')
        return f'{method}{headers}'

    def spot_policy_str(self) -> str:
        policy_strs: List[str] = []
        if (self.dynamic_ondemand_fallback is not None and
                self.dynamic_ondemand_fallback):
            if self.spot_placer is not None:
                if self.spot_placer == spot_placer_lib.SPOT_HEDGE_PLACER:
                    return 'SpotHedge'
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
        if self.spot_placer is not None:
            if not policy_strs:
                policy_strs.append('Spot placement')
            policy_strs.append(f'with {self.spot_placer} placer')
        if not policy_strs:
            return 'No spot policy'
        return ' '.join(policy_strs)

    def autoscaling_policy_str(self):
        # TODO(MaoZiming): Update policy_str
        min_plural = '' if self.min_replicas == 1 else 's'
        if self.max_replicas == self.min_replicas or self.max_replicas is None:
            return f'Fixed {self.min_replicas} replica{min_plural}'
        # Already checked in __init__.
        assert self.target_qps_per_replica is not None
        # TODO(tian): Refactor to contain more information
        max_plural = '' if self.max_replicas == 1 else 's'
        overprovision_str = ''
        if self.num_overprovision is not None:
            overprovision_str = (
                f' with {self.num_overprovision} overprovisioned replicas')
        return (f'Autoscaling from {self.min_replicas} to {self.max_replicas} '
                f'replica{max_plural}{overprovision_str} (target QPS per '
                f'replica: {self.target_qps_per_replica})')

    def set_ports(self, ports: str) -> None:
        self._ports = ports

    def tls_str(self):
        if self.tls_credential is None:
            return 'No TLS Enabled'
        return (f'Keyfile: {self.tls_credential.keyfile}, '
                f'Certfile: {self.tls_credential.certfile}')

    def __repr__(self) -> str:
        return textwrap.dedent(f"""\
            Readiness probe method:           {self.probe_str()}
            Readiness initial delay seconds:  {self.initial_delay_seconds}
            Readiness probe timeout seconds:  {self.readiness_timeout_seconds}
            Replica autoscaling policy:       {self.autoscaling_policy_str()}
            TLS Certificates:                 {self.tls_str()}
            Spot Policy:                      {self.spot_policy_str()}
            Load Balancing Policy:            {self.load_balancing_policy}
        """)

    @property
    def readiness_path(self) -> str:
        return self._readiness_path

    @property
    def initial_delay_seconds(self) -> int:
        return self._initial_delay_seconds

    @property
    def readiness_timeout_seconds(self) -> int:
        return self._readiness_timeout_seconds

    @property
    def min_replicas(self) -> int:
        return self._min_replicas

    @property
    def max_replicas(self) -> Optional[int]:
        # If None, treated as having the same value of min_replicas.
        return self._max_replicas

    @property
    def num_overprovision(self) -> Optional[int]:
        return self._num_overprovision

    @property
    def ports(self) -> Optional[str]:
        return self._ports

    @property
    def target_qps_per_replica(self) -> Optional[float]:
        return self._target_qps_per_replica

    @property
    def post_data(self) -> Optional[Dict[str, Any]]:
        return self._post_data

    @property
    def tls_credential(self) -> Optional[serve_utils.TLSCredential]:
        return self._tls_credential

    @tls_credential.setter
    def tls_credential(self,
                       value: Optional[serve_utils.TLSCredential]) -> None:
        self._tls_credential = value

    @property
    def readiness_headers(self) -> Optional[Dict[str, str]]:
        return self._readiness_headers

    @property
    def base_ondemand_fallback_replicas(self) -> Optional[int]:
        return self._base_ondemand_fallback_replicas

    @property
    def dynamic_ondemand_fallback(self) -> Optional[bool]:
        return self._dynamic_ondemand_fallback

    @property
    def spot_placer(self) -> Optional[str]:
        return self._spot_placer

    @property
    def upscale_delay_seconds(self) -> Optional[int]:
        return self._upscale_delay_seconds

    @property
    def downscale_delay_seconds(self) -> Optional[int]:
        return self._downscale_delay_seconds

    @property
    def use_ondemand_fallback(self) -> bool:
        return self._use_ondemand_fallback

    @property
    def load_balancing_policy(self) -> str:
        return lb_policies.LoadBalancingPolicy.make_policy_name(
            self._load_balancing_policy)
