"""SkyServe v2 service specification parser.

Parses the new YAML format that supports 4 tiers:
  - Tier 1: Just a model ID
  - Tier 2: Model + resource overrides
  - Tier 3: Full control with routing, autoscaling, PD disaggregation
  - Tier 4: Raw KServe passthrough
"""
import dataclasses
from typing import Any, Dict, List, Optional

import yaml


@dataclasses.dataclass
class LoRAAdapter:
    """LoRA adapter configuration."""
    name: str
    uri: str


@dataclasses.dataclass
class AutoscalingMetric:
    """Autoscaling metric definition."""
    type: str  # kv_cache_utilization, queue_depth, requests_per_second
    target: float


@dataclasses.dataclass
class AutoscalingConfig:
    """Autoscaling configuration."""
    metrics: List[AutoscalingMetric] = dataclasses.field(default_factory=list)
    upscale_delay: int = 60  # seconds
    downscale_delay: int = 300  # seconds


@dataclasses.dataclass
class RoutingConfig:
    """Routing configuration."""
    mode: str = 'round_robin'  # round_robin | kv_cache_aware
    disaggregated: bool = False  # Enable PD disaggregation


@dataclasses.dataclass
class PrefillConfig:
    """Prefill pool configuration for PD disaggregation."""
    accelerators: Optional[str] = None  # e.g., 'H100:4'
    replicas: int = 1


@dataclasses.dataclass
class ServiceConfig:
    """Service-level configuration."""
    name: Optional[str] = None
    replicas: int = 1
    max_replicas: Optional[int] = None
    routing: RoutingConfig = dataclasses.field(
        default_factory=RoutingConfig)
    autoscaling: Optional[AutoscalingConfig] = None
    prefill: Optional[PrefillConfig] = None
    lora_adapters: List[LoRAAdapter] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class ResourceConfig:
    """Resource configuration."""
    accelerators: Optional[str] = None  # e.g., 'H100:2', 'A100:4'

    @property
    def gpu_type(self) -> Optional[str]:
        if self.accelerators:
            parts = self.accelerators.split(':')
            return parts[0]
        return None

    @property
    def num_gpus(self) -> Optional[int]:
        if self.accelerators:
            parts = self.accelerators.split(':')
            if len(parts) > 1:
                return int(parts[1])
            return 1
        return None


@dataclasses.dataclass
class SkyServeSpec:
    """Parsed SkyServe v2 service specification."""
    # Model (Tier 1+)
    model: Optional[str] = None

    # Engine (Tier 2+)
    engine: str = 'vllm'
    engine_args: Dict[str, Any] = dataclasses.field(default_factory=dict)

    # Resources (Tier 2+)
    resources: ResourceConfig = dataclasses.field(
        default_factory=ResourceConfig)

    # Service config (Tier 2+)
    service: ServiceConfig = dataclasses.field(
        default_factory=ServiceConfig)

    # Raw KServe passthrough (Tier 4)
    kserve_raw: Optional[Dict[str, Any]] = None

    @property
    def is_passthrough(self) -> bool:
        """Whether this is a raw KServe passthrough spec."""
        return self.kserve_raw is not None

    @property
    def is_minimal(self) -> bool:
        """Whether this is a Tier 1 (model-only) spec."""
        return (self.model is not None and
                self.resources.accelerators is None and
                self.service.replicas == 1)


def parse_spec(yaml_path: str) -> SkyServeSpec:
    """Parse a SkyServe v2 YAML spec file.

    Supports all 4 tiers of specification.
    """
    with open(yaml_path, 'r') as f:
        data = yaml.safe_load(f)
    return parse_spec_from_dict(data)


def parse_spec_from_dict(data: Dict[str, Any]) -> SkyServeSpec:
    """Parse a SkyServe v2 spec from a dictionary."""
    # Tier 4: Raw KServe passthrough
    if 'kserve' in data:
        return SkyServeSpec(kserve_raw=data['kserve'])

    spec = SkyServeSpec()

    # Model
    spec.model = data.get('model')

    # Engine
    spec.engine = data.get('engine', 'vllm')
    spec.engine_args = data.get('engine_args', {})

    # Resources
    resources_data = data.get('resources', {})
    spec.resources = ResourceConfig(
        accelerators=resources_data.get('accelerators'),
    )

    # Service configuration
    service_data = data.get('service', {})
    spec.service = ServiceConfig(
        name=service_data.get('name'),
        replicas=service_data.get('replicas', 1),
        max_replicas=service_data.get('max_replicas'),
    )

    # Routing
    routing_data = service_data.get('routing', {})
    spec.service.routing = RoutingConfig(
        mode=routing_data.get('mode', 'round_robin'),
        disaggregated=routing_data.get('disaggregated', False),
    )

    # Autoscaling
    autoscaling_data = service_data.get('autoscaling')
    if autoscaling_data:
        metrics = []
        for m in autoscaling_data.get('metrics', []):
            metrics.append(AutoscalingMetric(
                type=m['type'],
                target=m['target'],
            ))
        spec.service.autoscaling = AutoscalingConfig(
            metrics=metrics,
            upscale_delay=autoscaling_data.get('upscale_delay', 60),
            downscale_delay=autoscaling_data.get('downscale_delay', 300),
        )

    # Prefill (PD disaggregation)
    prefill_data = service_data.get('prefill')
    if prefill_data:
        spec.service.prefill = PrefillConfig(
            accelerators=prefill_data.get('resources', {}).get(
                'accelerators'),
            replicas=prefill_data.get('replicas', 1),
        )

    # LoRA adapters
    lora_data = service_data.get('lora_adapters', [])
    for adapter in lora_data:
        spec.service.lora_adapters.append(LoRAAdapter(
            name=adapter['name'],
            uri=adapter['uri'],
        ))

    return spec


def validate_spec(spec: SkyServeSpec) -> List[str]:
    """Validate a SkyServe spec, returning a list of errors."""
    errors = []

    if spec.is_passthrough:
        if not isinstance(spec.kserve_raw, dict):
            errors.append('kserve passthrough must be a dictionary')
        return errors

    if spec.model is None:
        errors.append('model is required (or use kserve passthrough)')

    if spec.service.replicas < 1:
        errors.append('replicas must be >= 1')

    if (spec.service.max_replicas is not None and
            spec.service.max_replicas < spec.service.replicas):
        errors.append('max_replicas must be >= replicas')

    if spec.service.routing.disaggregated and spec.service.prefill is None:
        # Not an error -- we can auto-configure prefill
        pass

    if spec.resources.accelerators:
        parts = spec.resources.accelerators.split(':')
        if len(parts) > 2:
            errors.append(
                f'Invalid accelerators format: {spec.resources.accelerators}.'
                ' Expected format: GPU_TYPE:COUNT (e.g., H100:4)')

    return errors
