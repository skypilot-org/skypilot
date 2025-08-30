"""Responses for the API server."""

import enum
from typing import Any, Dict, List, Optional

import pydantic

from sky import models
from sky.server import common
from sky.utils import status_lib


class ResponseBaseModel(pydantic.BaseModel):
    """A pydantic model that acts like a dict.

    Supports the following syntax:
    class SampleResponse(DictLikePayload):
        field: str

    response = SampleResponse(field='value')
    print(response['field']) # prints 'value'
    response['field'] = 'value2'
    print(response['field']) # prints 'value2'
    print('field' in response) # prints True

    This model exists for backwards compatibility with the
    old SDK that used to return a dict.

    The backward compatibility may be removed
    in the future.
    """
    # Ignore extra fields in the request body, which is useful for backward
    # compatibility. The difference with `allow` is that `ignore` will not
    # include the unknown fields when dump the model, i.e., we can add new
    # fields to the request body without breaking the existing old API server
    # where the handler function does not accept the new field in function
    # signature.
    model_config = pydantic.ConfigDict(extra='ignore')

    # backward compatibility with dict
    # TODO(syang): remove this in v0.13.0
    def __getitem__(self, key):
        try:
            return getattr(self, key)
        except AttributeError as e:
            raise KeyError(key) from e

    def __setitem__(self, key, value):
        setattr(self, key, value)

    def get(self, key, default=None):
        return getattr(self, key, default)

    def __contains__(self, key):
        return hasattr(self, key)

    def keys(self):
        return self.model_dump().keys()

    def values(self):
        return self.model_dump().values()

    def items(self):
        return self.model_dump().items()

    def __repr__(self):
        return self.__dict__.__repr__()


class APIHealthResponse(ResponseBaseModel):
    """Response for the API health endpoint."""
    status: common.ApiServerStatus
    api_version: str = ''
    version: str = ''
    version_on_disk: str = ''
    commit: str = ''
    basic_auth_enabled: bool = False
    user: Optional[models.User] = None


class StatusResponse(ResponseBaseModel):
    """Response for the status endpoint."""
    name: str
    launched_at: int
    # pydantic cannot generate the pydantic-core schema for
    # backends.ResourceHandle, so we use Any here.
    # This is an internally facing field anyway, so it's less
    # of a problem that it's not typed.
    handle: Any
    last_use: str
    status: status_lib.ClusterStatus
    autostop: int
    to_down: bool
    owner: Optional[List[str]] = None
    # metadata is a JSON, so we use Any here.
    metadata: Optional[Dict[str, Any]] = None
    cluster_hash: str
    # pydantic cannot generate the pydantic-core schema for
    # storage_mounts_metadata, so we use Any here.
    storage_mounts_metadata: Optional[Dict[str, Any]] = None
    cluster_ever_up: bool
    status_updated_at: int
    user_hash: str
    user_name: str
    config_hash: Optional[str] = None
    workspace: str
    last_creation_yaml: Optional[str] = None
    last_creation_command: Optional[str] = None
    is_managed: bool
    last_event: Optional[str] = None
    resources_str: Optional[str] = None
    resources_str_full: Optional[str] = None
    # credentials is a JSON, so we use Any here.
    credentials: Optional[Dict[str, Any]] = None
    nodes: int
    cloud: Optional[str] = None
    region: Optional[str] = None
    cpus: Optional[str] = None
    memory: Optional[str] = None
    accelerators: Optional[str] = None


class UploadStatus(enum.Enum):
    """Status of the upload."""
    UPLOADING = 'uploading'
    COMPLETED = 'completed'
