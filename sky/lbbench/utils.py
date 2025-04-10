"""Common utilities for the load balancer benchmark."""

import dataclasses
from typing import Dict, List, Optional

OAIChatHistory = List[Dict[str, str]]


@dataclasses.dataclass
class Metric:
    """Metric for each request."""
    uid: int
    start: Optional[float] = None
    end: Optional[float] = None
    ttft: Optional[float] = None
    e2e_latency: Optional[float] = None
    failed: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: int = 0
    headers: Optional[Dict[str, str]] = None
    cached_tokens: Optional[int] = None


def get_one_round(role: str, content: str) -> Dict[str, str]:
    return {'role': role, 'content': content}
