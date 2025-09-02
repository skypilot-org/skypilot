"""Global environment options for sky."""
import enum
import os
from typing import Dict


class Options(enum.Enum):
    """Environment variables for SkyPilot."""

    # (env var name, default value)
    IS_DEVELOPER = ('SKYPILOT_DEV', False)
    SHOW_DEBUG_INFO = ('SKYPILOT_DEBUG', False)
    DISABLE_LOGGING = ('SKYPILOT_DISABLE_USAGE_COLLECTION', False)
    MINIMIZE_LOGGING = ('SKYPILOT_MINIMIZE_LOGGING', True)
    SUPPRESS_SENSITIVE_LOG = ('SKYPILOT_SUPPRESS_SENSITIVE_LOG', False)
    # Internal: this is used to skip the cloud user identity check, which is
    # used to protect cluster operations in a multi-identity scenario.
    # Currently, this is only used in the job and serve controller, as there
    # will not be multiple identities, and skipping the check can increase
    # robustness.
    SKIP_CLOUD_IDENTITY_CHECK = ('SKYPILOT_SKIP_CLOUD_IDENTITY_CHECK', False)
    # TODO(tian): Hack. This is for let the external LB inherit this option.
    # This should not be included in the controller envs.
    DO_PUSHING_ACROSS_LB = ('DO_PUSHING_ACROSS_LB', False)
    LB_PUSHING_ENABLE_LB = ('LB_PUSHING_ENABLE_LB', True)
    DO_PUSHING_TO_REPLICA = ('DO_PUSHING_TO_REPLICA', False)
    USE_V2_STEALING = ('USE_V2_STEALING', False)
    ENABLE_SELECTIVE_PUSHING = ('ENABLE_SELECTIVE_PUSHING', False)
    DISABLE_LEAST_LOAD_IN_PREFIX = ('DISABLE_LEAST_LOAD_IN_PREFIX', False)
    USE_IE_QUEUE_INDICATOR = ('USE_IE_QUEUE_INDICATOR', True)
    FORCE_DISABLE_STEALING = ('FORCE_DISABLE_STEALING', False)

    def __init__(self, env_var: str, default: bool) -> None:
        self.env_var = env_var
        self.default = default

    def __repr__(self) -> str:
        return self.env_var

    def get(self) -> bool:
        """Check if an environment variable is set to True."""
        return os.getenv(self.env_var,
                         str(self.default)).lower() in ('true', '1')

    @property
    def env_key(self) -> str:
        """The environment variable key name."""
        return self.value[0]

    @classmethod
    def all_options(cls) -> Dict[str, bool]:
        """Returns all options as a dictionary."""
        return {option.env_key: option.get() for option in list(Options)}
