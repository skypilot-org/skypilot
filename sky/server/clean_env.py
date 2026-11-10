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
from typing import Dict, Mapping, Optional

# Set once via capture_clean_server_env() in the main API server process, and
# once per worker via executor_initializer (forwarded from the main process's
# snapshot through initargs). Reads happen via get_clean_server_env().
_clean_server_env: Optional[Dict[str, str]] = None

# Never forwarded to a spawned subprocess: prometheus_client reads
# PROMETHEUS_MULTIPROC_DIR as "join the parent's multiprocess registry", and
# the per-pid files a child writes outlive it -- mark_process_dead() reclaims
# only the gauge_live* ones -- so every short-lived subprocess permanently
# grows the directory /metrics merges on each scrape.
_DROP_FOR_SUBPROCESSES = frozenset({'PROMETHEUS_MULTIPROC_DIR'})


def _for_subprocesses(env: Mapping[str, str]) -> Dict[str, str]:
    return {k: v for k, v in env.items() if k not in _DROP_FOR_SUBPROCESSES}


def capture_clean_server_env() -> None:
    """Snapshot os.environ as the clean server env. Idempotent.

    Called from the main API server process at startup, before any request
    can mutate os.environ. Workers don't call this — they receive the same
    snapshot through executor_initializer's initargs and set
    _clean_server_env directly.
    """
    global _clean_server_env
    if _clean_server_env is None:
        _clean_server_env = _for_subprocesses(os.environ)


def set_clean_server_env(env: Dict[str, str]) -> None:
    """Adopt an externally-provided clean env snapshot. Idempotent.

    Used by worker processes to install the snapshot they received from the
    main API server process via executor_initializer's initargs.
    """
    global _clean_server_env
    if _clean_server_env is None:
        _clean_server_env = _for_subprocesses(env)


def get_clean_server_env() -> Optional[Dict[str, str]]:
    """Return a copy of the server's pre-request-pollution env, or None.

    None means no snapshot was captured in this process — which is the
    correct state outside the API server (e.g. a Slurm skylet running a
    LocalProcessCommandRunner against the local node). Callers should pass
    the result through to `subprocess.Popen(env=...)`: a real dict on the
    API server gives spawned subprocesses the clean snapshot, and None
    falls back to inheriting `os.environ`, which is the right default in
    non-API-server contexts.
    """
    if _clean_server_env is None:
        return None
    return dict(_clean_server_env)
