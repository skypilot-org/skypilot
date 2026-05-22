"""Process-wide snapshot of the API server's pre-pollution env.

This module is intentionally a leaf (no sky imports beyond stdlib) so that
`sky.utils.command_runner` can import it at module load time without
re-entering the heavy `sky.server.requests.executor` graph, which would
trigger a circular import (executor's transitive imports include
`sky.provision.kubernetes.instance`, which references
`command_runner.CommandRunner` at module top level).

Used by `LocalProcessCommandRunner.run` to set the `env` for spawned
subprocesses (consolidation-mode controllers) so they don't inherit
per-request env pollution from `override_request_env_and_config`.
"""
import os
from typing import Dict, Optional

# Set once via capture_clean_server_env() in the main API server process, and
# once per worker via executor_initializer (forwarded from the main process's
# snapshot through initargs). Reads happen via get_clean_server_env().
_clean_server_env: Optional[Dict[str, str]] = None


def capture_clean_server_env() -> None:
    """Snapshot os.environ as the clean server env. Idempotent.

    Called from the main API server process at startup, before any request
    can mutate os.environ. Workers don't call this — they receive the same
    snapshot through executor_initializer's initargs and set
    _clean_server_env directly.
    """
    global _clean_server_env
    if _clean_server_env is None:
        _clean_server_env = dict(os.environ)


def set_clean_server_env(env: Dict[str, str]) -> None:
    """Adopt an externally-provided clean env snapshot. Idempotent.

    Used by worker processes to install the snapshot they received from the
    main API server process via executor_initializer's initargs.
    """
    global _clean_server_env
    if _clean_server_env is None:
        _clean_server_env = dict(env)


def get_clean_server_env() -> Dict[str, str]:
    """Return a copy of the server's pre-request-pollution env.

    Used by LocalProcessCommandRunner.run as the env for spawned
    subprocesses, so consolidation-mode controllers don't inherit
    per-request env mutations applied by override_request_env_and_config.

    Raises RuntimeError if the snapshot was never installed. The only
    legitimate caller is on the API server (main process: post
    capture_clean_server_env() at startup; worker process: post
    set_clean_server_env() in executor_initializer). Falling back to
    os.environ here would silently re-introduce the leak this module
    exists to prevent.
    """
    if _clean_server_env is None:
        raise RuntimeError(
            'get_clean_server_env() called before the snapshot was '
            'installed. The main API server process must call '
            'capture_clean_server_env() at startup, and workers must call '
            'set_clean_server_env() from executor_initializer.')
    return dict(_clean_server_env)
