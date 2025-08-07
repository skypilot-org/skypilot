"""Responses for the API server."""

from typing import Optional

from pydantic import BaseModel

from sky import models
from sky.server import common


class APIHealthResponse(BaseModel):
    """Response for the API health endpoint."""
    status: common.ApiServerStatus
    api_version: Optional[str] = None
    version: Optional[str] = None
    version_on_disk: Optional[str] = None
    commit: Optional[str] = None
    user: Optional[models.User] = None
    basic_auth_enabled: Optional[bool] = None
