"""Responses for the API server."""

from typing import Any, Dict, Optional

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


class APIHealthResponse(ResponseBaseModel):
    """Response for the API health endpoint.
    {
        'status': (str) The status of the API server.
        'api_version': (str) The API version of the API server.
        'version': (str) The version of SkyPilot used for API server.
        'version_on_disk': (str) The version of the SkyPilot installation on
          disk, which can be used to warn about restarting the API server
        'commit': (str) The commit hash of SkyPilot used for API server.
        'basic_auth_enabled': (bool) Whether basic authentication is enabled.
        'user': (User) Information about the user that made the request.
    }
    """
    status: common.ApiServerStatus
    api_version: str = ''
    version: str = ''
    version_on_disk: str = ''
    commit: str = ''
    basic_auth_enabled: bool = False
    user: Optional[models.User] = None


class StatusResponse(ResponseBaseModel):
    """Response for the status endpoint.
    {
        'name': (str) cluster name,
        'launched_at': (int) timestamp of last launch on this cluster,
        'handle': (ResourceHandle) an internal handle to the cluster,
        'last_use': (str) the last command/entrypoint that affected this
            cluster,
        'status': (sky.ClusterStatus) cluster status,
        'autostop': (int) idle time before autostop,
        'to_down': (bool) whether autodown is used instead of autostop,
        'metadata': (dict) metadata of the cluster,
        'user_hash': (str) user hash of the cluster owner,
        'user_name': (str) user name of the cluster owner,
        'resources_str': (str) the resource string representation of the
            cluster,
        'storage_mounts_metadata': (dict) storage mounts
            metadata of the cluster
    }
    """
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
    owner: Any
    metadata: dict
    cluster_hash: str
    # pydantic cannot generate the pydantic-core schema for
    # storage_mounts_metadata, so we use Any here.
    storage_mounts_metadata: Optional[Dict[str, Any]]    
    cluster_ever_up: bool
    status_updated_at: int
    user_hash: str
    user_name: str
    config_hash: Optional[str]
    workspace: str
    last_creation_yaml: Optional[str]
    last_creation_command: str
    is_managed: bool
    last_event: str
    resources_str: str
    resources_str_full: str
    credentials: Optional[Dict[str, Any]]
    nodes: int
    cloud: str
    region: str
    cpus: float
    memory: Any
    accelerators: Any
