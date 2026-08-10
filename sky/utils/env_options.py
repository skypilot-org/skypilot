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
    # Internal: This environment variable is set to "true" by Buildkite
    # agent when running tests. It is used to identify when SkyPilot is
    # running in a Buildkite container environment, which requires special
    # handling for networking between containers.
    RUNNING_IN_BUILDKITE = ('BUILDKITE', False)
    # Internal: This is used for testing to enable grpc for communication
    # between the API server and the Skylet.
    ENABLE_GRPC = ('SKYPILOT_ENABLE_GRPC', False)
    # Allow all contexts for Kubernetes if allowed_contexts is not set in
    # config.
    ALLOW_ALL_KUBERNETES_CONTEXTS = ('SKYPILOT_ALLOW_ALL_KUBERNETES_CONTEXTS',
                                     False)
    # Disable starting a local API server on this machine, including the
    # silent auto-start that happens when a client command finds no API
    # server endpoint configured. When set, any code path that would start
    # a local API server fails with an actionable error instead. Useful in
    # managed environments (CI runners, dev containers, in-cluster pods)
    # where an implicitly started local API server could schedule real
    # workloads using ambient credentials (e.g. a pod ServiceAccount).
    DISABLE_LOCAL_API_SERVER = ('SKYPILOT_DISABLE_LOCAL_API_SERVER', False)
    # Whether `allowed_contexts: 'all'` (or the env-var-triggered allow-all
    # path) should include the API server's own in-cluster context. Default
    # `True` (backward compatible). Set to `false` on the API server pod to
    # keep the in-cluster context from being surfaced as a user-facing
    # compute target via `allowed_contexts: 'all'`.
    ALL_KUBERNETES_CONTEXTS_INCLUDES_IN_CLUSTER = (
        'SKYPILOT_ALL_KUBERNETES_CONTEXTS_INCLUDES_IN_CLUSTER', True)

    def __init__(self, env_var: str, default: bool) -> None:
        super().__init__()
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
