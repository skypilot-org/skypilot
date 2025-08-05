import dataclasses
from typing import Literal, Optional

from sky.server import common
from sky import models


@dataclasses.dataclass
class APIHealthResponse:
    status: Literal[common.ApiServerStatus.NEEDS_AUTH, common.ApiServerStatus.HEALTHY]
    api_version: str
    version: str
    version_on_disk: str
    commit: str
    user: Optional[models.User]
    basic_auth_enabled: bool

