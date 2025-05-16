"""Common utilities for the load balancer benchmark."""

import dataclasses
from typing import Any, Dict, List, Optional

import sky

OAIChatHistory = List[Dict[str, str]]

# sgl_cluster = 'sgl-router'
# sky_sgl_enhanced_cluster = 'sgl-router-pull'
# vanilla_least_load_cluster = 'vanilla-least-load'
# global_least_load_cluster = 'global-least-load'
# consistent_hashing_cluster = 'consistent-hashing'
# consistent_hashing_enhanced_cluster = 'consistent-hashing-enhanced'

single_lb_clusters = [
    'sgl-router',
    'sgl-router-pull',
    # 'sgl-router-no-fallback',
    'sgl-router-fix-max-concurrency',
    'vanilla-least-load',
    'global-least-load',
    'consistent-hashing',
    'consistent-hashing-enhanced',
    'round-robin',
    'round-robin-enhanced',
]
single_lb_clusters = [
    'sgl-router',
    'sgl-router-sp-p',
    'sgl-router-sp-c-40',
    'sgl-router-sp-c-50',
    'sgl-router-sp-c-60',
    'sgl-router-sp-c-70',
]


def _get_sp_c_envs(max_concurrent_requests: int) -> Dict[str, str]:
    return {
        'FORCE_DISABLE_STEALING': 'true',
        'USE_IE_QUEUE_INDICATOR': 'false',
        'DO_PUSHING_TO_REPLICA': 'true',
        'MAX_CONCURRENT_REQUESTS': str(max_concurrent_requests),
    }


single_lb_policy_and_extra_args = [
    (None, None),
    ('prefix_tree', None),
    # ('prefix_tree', {
    #     'DISABLE_LEAST_LOAD_IN_PREFIX': 'true'
    # }),
    ('prefix_tree', _get_sp_c_envs(60)),
    ('least_load', {
        'DISABLE_SELECTIVE_PUSHING': 'true'
    }),
    ('least_load', None),
    ('consistent_hashing', {
        'DISABLE_SELECTIVE_PUSHING': 'true'
    }),
    ('consistent_hashing', None),
    ('round_robin', {
        'DISABLE_SELECTIVE_PUSHING': 'true'
    }),
    ('round_robin', None),
]
single_lb_policy_and_extra_args = [
    (None, None),
    ('prefix_tree', None),
    ('prefix_tree', _get_sp_c_envs(40)),
    ('prefix_tree', _get_sp_c_envs(50)),
    ('prefix_tree', _get_sp_c_envs(60)),
    ('prefix_tree', _get_sp_c_envs(70)),
]


def sky_serve_status() -> List[Dict[str, Any]]:
    req = sky.serve.status(None)
    return sky.client.sdk.get(req)


def sky_status() -> List[Dict[str, Any]]:
    req = sky.status()
    return sky.client.sdk.get(req)


@dataclasses.dataclass
class Metric:
    """Metric for each request."""
    uid: str
    start: Optional[float] = None
    response_start: Optional[float] = None
    streaming_start: Optional[float] = None
    end: Optional[float] = None
    ttft: Optional[float] = None
    e2e_latency: Optional[float] = None
    failed: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: int = 0
    headers: Optional[Dict[str, str]] = None
    cached_tokens: Optional[int] = None
    hash_key: Optional[str] = None
    program_id: Optional[str] = None


def get_one_round(role: str, content: str) -> Dict[str, str]:
    return {'role': role, 'content': content}
