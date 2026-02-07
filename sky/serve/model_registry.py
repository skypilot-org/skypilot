"""Model registry for SkyServe v2.

Maps HuggingFace model IDs to resource requirements including GPU type, count,
tensor parallelism, and engine arguments.
"""
import dataclasses
import logging
import math
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Bytes per parameter for different dtypes
BYTES_PER_PARAM = {
    'float32': 4,
    'float16': 2,
    'bfloat16': 2,
    'int8': 1,
    'int4': 0.5,
}

# GPU memory capacities in GB
GPU_MEMORY_GB = {
    'H100': 80,
    'H100_NVLINK_80GB': 80,
    'H100_SXM': 80,
    'A100': 80,
    'A100-80GB': 80,
    'A100-40GB': 40,
    'A10G': 24,
    'L4': 24,
    'L40': 48,
    'L40S': 48,
    'T4': 16,
    'V100': 16,
}


@dataclasses.dataclass
class ModelConfig:
    """Configuration resolved for a model."""
    model_id: str
    # Resource requirements
    gpu_type: str  # e.g., 'H100', 'A100'
    num_gpus: int
    tensor_parallel: int
    # Engine configuration
    engine: str  # e.g., 'vllm'
    engine_args: Dict[str, Any] = dataclasses.field(default_factory=dict)
    # Model metadata
    param_count_billions: Optional[float] = None
    default_dtype: str = 'bfloat16'
    max_model_len: Optional[int] = None
    # Container image
    image: str = 'vllm/vllm-openai:latest'

    @property
    def model_size_gb(self) -> float:
        """Estimated model size in GB."""
        if self.param_count_billions:
            bytes_per_param = BYTES_PER_PARAM.get(self.default_dtype, 2)
            return self.param_count_billions * bytes_per_param
        return 0

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


# Built-in registry of popular models
# Format: model_id -> (param_count_billions, default_dtype, max_model_len,
#                      recommended_gpu, recommended_num_gpus, extra_engine_args)
_BUILTIN_REGISTRY: Dict[str, Dict[str, Any]] = {
    # Llama 3.x family
    'meta-llama/Llama-3.1-8B-Instruct': {
        'param_count_billions': 8,
        'max_model_len': 8192,
        'min_gpus': 1,
        'recommended_gpu': 'H100',
    },
    'meta-llama/Llama-3.1-70B-Instruct': {
        'param_count_billions': 70,
        'max_model_len': 8192,
        'min_gpus': 4,
        'recommended_gpu': 'H100',
    },
    'meta-llama/Llama-3.1-405B-Instruct': {
        'param_count_billions': 405,
        'max_model_len': 4096,
        'min_gpus': 8,
        'recommended_gpu': 'H100',
    },
    'meta-llama/Llama-3.2-1B-Instruct': {
        'param_count_billions': 1.2,
        'max_model_len': 8192,
        'min_gpus': 1,
        'recommended_gpu': 'A100',
    },
    'meta-llama/Llama-3.2-3B-Instruct': {
        'param_count_billions': 3.2,
        'max_model_len': 8192,
        'min_gpus': 1,
        'recommended_gpu': 'A100',
    },
    # Mistral/Mixtral
    'mistralai/Mistral-7B-Instruct-v0.3': {
        'param_count_billions': 7,
        'max_model_len': 8192,
        'min_gpus': 1,
        'recommended_gpu': 'A100',
    },
    'mistralai/Mixtral-8x7B-Instruct-v0.1': {
        'param_count_billions': 47,
        'max_model_len': 8192,
        'min_gpus': 2,
        'recommended_gpu': 'H100',
    },
    # Qwen
    'Qwen/Qwen2.5-7B-Instruct': {
        'param_count_billions': 7,
        'max_model_len': 8192,
        'min_gpus': 1,
        'recommended_gpu': 'A100',
    },
    'Qwen/Qwen2.5-72B-Instruct': {
        'param_count_billions': 72,
        'max_model_len': 8192,
        'min_gpus': 4,
        'recommended_gpu': 'H100',
    },
    # DeepSeek
    'deepseek-ai/DeepSeek-R1': {
        'param_count_billions': 671,
        'max_model_len': 4096,
        'min_gpus': 8,
        'recommended_gpu': 'H100',
        'extra_engine_args': {
            'trust_remote_code': True,
        },
    },
    # Gemma
    'google/gemma-2-9b-it': {
        'param_count_billions': 9,
        'max_model_len': 8192,
        'min_gpus': 1,
        'recommended_gpu': 'A100',
    },
    'google/gemma-2-27b-it': {
        'param_count_billions': 27,
        'max_model_len': 8192,
        'min_gpus': 2,
        'recommended_gpu': 'A100',
    },
}


def _compute_gpu_requirements(param_count_billions: float,
                              dtype: str = 'bfloat16',
                              gpu_type: str = 'H100') -> int:
    """Compute number of GPUs needed for a model.

    Uses a simple heuristic: model_size_gb * 1.2 (overhead) / gpu_memory.
    """
    bytes_per_param = BYTES_PER_PARAM.get(dtype, 2)
    model_size_gb = param_count_billions * bytes_per_param
    # Add 20% overhead for KV cache, activations, etc.
    required_gb = model_size_gb * 1.2
    gpu_mem = GPU_MEMORY_GB.get(gpu_type, 80)
    num_gpus = max(1, math.ceil(required_gb / gpu_mem))
    # Round up to power of 2 for tensor parallelism
    tp_sizes = [1, 2, 4, 8]
    for tp in tp_sizes:
        if tp >= num_gpus:
            return tp
    return 8


def resolve_model_config(
    model_id: str,
    gpu_type: Optional[str] = None,
    num_gpus: Optional[int] = None,
    engine: str = 'vllm',
    engine_args: Optional[Dict[str, Any]] = None,
    max_model_len: Optional[int] = None,
) -> ModelConfig:
    """Resolve a model ID to a full ModelConfig.

    Priority:
    1. User overrides (gpu_type, num_gpus, engine_args)
    2. Built-in registry
    3. Auto-computed from parameter count
    """
    registry_entry = _BUILTIN_REGISTRY.get(model_id, {})

    # Determine parameter count
    param_count = registry_entry.get('param_count_billions')
    if param_count is None:
        # Try to infer from model name
        param_count = _infer_param_count(model_id)

    # Determine GPU type
    resolved_gpu_type = gpu_type or registry_entry.get(
        'recommended_gpu', 'H100')

    # Determine number of GPUs
    if num_gpus is not None:
        resolved_num_gpus = num_gpus
    elif param_count:
        min_gpus = registry_entry.get('min_gpus')
        if min_gpus:
            resolved_num_gpus = min_gpus
        else:
            resolved_num_gpus = _compute_gpu_requirements(
                param_count, gpu_type=resolved_gpu_type)
    else:
        resolved_num_gpus = 1

    # Tensor parallelism = num GPUs
    tensor_parallel = resolved_num_gpus

    # Resolve max_model_len
    resolved_max_model_len = (max_model_len
                              or registry_entry.get('max_model_len', 8192))

    # Build engine args
    resolved_engine_args = {}
    extra = registry_entry.get('extra_engine_args', {})
    if extra:
        resolved_engine_args.update(extra)
    if engine_args:
        resolved_engine_args.update(engine_args)

    return ModelConfig(
        model_id=model_id,
        gpu_type=resolved_gpu_type,
        num_gpus=resolved_num_gpus,
        tensor_parallel=tensor_parallel,
        engine=engine,
        engine_args=resolved_engine_args,
        param_count_billions=param_count,
        max_model_len=resolved_max_model_len,
    )


def _infer_param_count(model_id: str) -> Optional[float]:
    """Try to infer parameter count from model name.

    Looks for patterns like '7B', '70B', '8b', etc. in the model ID.
    """
    import re
    # Match patterns like 7B, 70B, 1.2B, etc.
    match = re.search(r'(\d+\.?\d*)[Bb]', model_id.split('/')[-1])
    if match:
        return float(match.group(1))
    return None


def list_supported_models() -> List[str]:
    """Return list of models in the built-in registry."""
    return sorted(_BUILTIN_REGISTRY.keys())
