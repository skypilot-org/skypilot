"""This file defines the schemas for the requests to the controller.
"""

import time
import typing
from typing import List

import pydantic

from sky.serve import serve_utils

if typing.TYPE_CHECKING:
    import fastapi


class RequestsAggregator(pydantic.BaseModel):
    """RequestsAggregator: Base class for request aggregators."""
    def add(self, request: 'fastapi.Request') -> None:
        raise NotImplementedError

    def clear(self) -> None:
        raise NotImplementedError

class TimestampAggregator(RequestsAggregator):
    """TimestampAggregator: Aggregates request timestamps.
    This is useful for QPS-based autoscaling.
    """
    timestamps: List[float] = pydantic.Field(default_factory=list)

    def add(self, request: 'fastapi.Request') -> None:
        del request  # unused
        self.timestamps.append(time.time())

    def clear(self) -> None:
        self.timestamps.clear()

class LoadBalancerRequest(pydantic.BaseModel):
    """LoadBalancerRequest: Request to load balance."""
    request_aggregator_info: RequestsAggregator

    @pydantic.field_validator('request_aggregator_info', mode='before')
    def parse_request_aggregator(cls, value):
        if 'timestamps' in value:
            return TimestampAggregator(**value)
        else:
            raise ValueError('Unsupported request aggregator type')


class UpdateServiceRequest(pydantic.BaseModel):
    version: int
    mode: serve_utils.UpdateMode = serve_utils.DEFAULT_UPDATE_MODE


class TerminateReplicaRequest(pydantic.BaseModel):
    replica_id: int
    purge: bool
