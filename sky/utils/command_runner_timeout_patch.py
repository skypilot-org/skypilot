"""Patch KubernetesCommandRunner.run to enforce a subprocess timeout on the
status-check path, preventing leaked kubectl-exec processes.

Background
----------
Status checks wrap `asyncio.to_thread(runner.run)` in
`asyncio.wait_for(timeout=_JOB_STATUS_FETCH_TIMEOUT_SECONDS)`. When the
wait_for fires, the Python future is cancelled from the caller's view, but
the underlying thread keeps running and the `subprocess.Popen` it issued
(kubectl exec) keeps running too: nothing has called `proc.kill()`. The
leaked kubectl child holds a TCP connection to the kube-apiserver, occupies
a slot in the urllib3/sock pool, and continues consuming CPU on bench-api
until it eventually times out on its own. Under a heavy launch / recovery
load this snowballs: 36% of kubectl processes older than the 30s timeout in
the T1p storm200 experiment.

Fix
---
Monkey-patch `KubernetesCommandRunner.run` so the status-check call passes a
`timeout=` kwarg through to `log_lib.run_with_log`. That path already does
the right thing on timeout: a `threading.Timer` fires
`subprocess_utils.kill_children_processes(proc.pid)`, then the function
raises `subprocess.TimeoutExpired`. The patch catches it and re-raises as
`asyncio.TimeoutError` so the existing `_fetch_job_status` except clause
treats it as a transient error exactly as before.

Scope
-----
Only the status-check command is intercepted (matched by the unique
substring `'job_lib.get_statuses_payload'`). Other `runner.run()` callsites
(provisioning, setup, file transfer, etc.) legitimately run for many
minutes and must not be capped by a default timeout.
"""
import asyncio
import os
import subprocess

from sky.utils.command_runner import KubernetesCommandRunner

_orig_run = KubernetesCommandRunner.run

# Set slightly below `_JOB_STATUS_FETCH_TIMEOUT_SECONDS` so subprocess
# TimeoutExpired fires first and the run_with_log timer kills the kubectl
# child cleanly, before `asyncio.wait_for` cancels the future (which would
# leak the subprocess).
_DEFAULT_TIMEOUT_S = int(
    os.environ.get('SKYPILOT_KUBECTL_EXEC_TIMEOUT_S', '110'))

# Fingerprint of the status-check command issued by `backend.get_job_status`.
_STATUS_CHECK_FINGERPRINT = 'job_lib.get_statuses_payload'


def _is_status_check(cmd) -> bool:
    if isinstance(cmd, list):
        cmd = ' '.join(str(x) for x in cmd)
    return isinstance(cmd, str) and _STATUS_CHECK_FINGERPRINT in cmd


def _run_with_timeout(self, cmd, **kwargs):
    if 'timeout' not in kwargs and _is_status_check(cmd):
        kwargs['timeout'] = _DEFAULT_TIMEOUT_S
    try:
        return _orig_run(self, cmd, **kwargs)
    except subprocess.TimeoutExpired:
        raise asyncio.TimeoutError() from None


KubernetesCommandRunner.run = _run_with_timeout
