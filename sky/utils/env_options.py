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
