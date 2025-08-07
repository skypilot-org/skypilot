"""Responses for the API server."""

from typing import Optional

import pydantic

from sky import models
from sky.server import common


class DictLikeModel(pydantic.BaseModel):
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
    """
    # Ignore extra fields in the request body, which is useful for backward
    # compatibility. The difference with `allow` is that `ignore` will not
    # include the unknown fields when dump the model, i.e., we can add new
    # fields to the request body without breaking the existing old API server
    # where the handler function does not accept the new field in function
    # signature.
    model_config = pydantic.ConfigDict(extra='ignore')

        # backward compatibility with dict
    def __getitem__(self, key):
        try:
            return getattr(self, key)
        except AttributeError as e:
            raise KeyError(key) from e

    def __setitem__(self, key, value):
        setattr(self, key, value)

    def __contains__(self, key):
        return hasattr(self, key)


class APIHealthResponse(DictLikeModel):
    """Response for the API health endpoint."""
    status: common.ApiServerStatus
    api_version: str
    version: str
    version_on_disk: str
    commit: str
    basic_auth_enabled: bool
    user: Optional[models.User] = None
