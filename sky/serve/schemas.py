"""This file defines the schemas for the requests to the controller.
"""

import time
import typing
from typing import Any, Dict, List, Literal

import pydantic

from sky.serve import serve_utils

if typing.TYPE_CHECKING:
    import fastapi


class RequestsAggregator(pydantic.BaseModel):
    """RequestsAggregator: Base class for request aggregators."""
    type: str

    def add(self, request: 'fastapi.Request') -> None:
        raise NotImplementedError

    def clear(self) -> None:
        raise NotImplementedError


class TimestampAggregator(RequestsAggregator):
    """TimestampAggregator: Aggregates request timestamps.
    This is useful for QPS-based autoscaling.
    """
    type: Literal['timestamp'] = 'timestamp'
    timestamps: List[float] = pydantic.Field(default_factory=list)

    def add(self, request: 'fastapi.Request') -> None:
        del request  # unused
        self.timestamps.append(time.time())

    def clear(self) -> None:
        self.timestamps.clear()


class OtherAggregator(RequestsAggregator):
    """OtherAggregator: Aggregates other types of requests."""
    type: Literal['other'] = 'other'
    data: Dict[str, Any] = pydantic.Field(default_factory=dict)

    def add(self, request: 'fastapi.Request') -> None:
        raise NotImplementedError

    def clear(self) -> None:
        raise NotImplementedError


class LoadBalancerRequest(pydantic.BaseModel):
    """LoadBalancerRequest: Request to load balance."""
    request_aggregator: RequestsAggregator = pydantic.Field(
        ..., discriminator='type')


class UpdateServiceRequest(pydantic.BaseModel):
    version: int
    mode: serve_utils.UpdateMode = serve_utils.DEFAULT_UPDATE_MODE


class TerminateReplicaRequest(pydantic.BaseModel):
    replica_id: int
    purge: bool
