"""This file defines the schemas for the requests to the controller.
"""

from typing import List

import pydantic

from sky.serve import serve_utils


class RequestAggregator(pydantic.BaseModel):
    timestamps: List[float]


class LoadBalancerRequest(pydantic.BaseModel):
    request_aggregator: RequestAggregator


class UpdateServiceRequest(pydantic.BaseModel):
    version: int
    mode: serve_utils.UpdateMode = serve_utils.DEFAULT_UPDATE_MODE


class TerminateReplicaRequest(pydantic.BaseModel):
    replica_id: int
    purge: bool
