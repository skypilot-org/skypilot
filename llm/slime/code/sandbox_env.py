"""SkyPilot-sandbox Environment for mini-swe-agent, plus pool/claim helpers.

SandboxEnvironment satisfies the ``minisweagent.Environment`` protocol
(minisweagent/__init__.py:61-70: execute / get_template_vars / serialize) and
is structured after the installed ``environments/docker.py`` -- pydantic
config, sync ``execute(action, cwd, timeout)``, ``Submitted`` sentinel check
-- with docker-exec swapped for the sky.sandbox SDK's sync exec surface
(``Sandbox.exec(*argv, workdir=..., timeout_seconds=...) -> ExecHandle``,
``handle.wait()`` + ``handle.stdout.read()``).

Sync on purpose: the DefaultAgent loop runs inside ``asyncio.to_thread`` (one
thread per in-flight rollout), so everything here uses the SDK's plain sync
entrypoints. Output truncation is NOT done here: upstream's
mini_textbased.yaml observation template already elides >10k-char outputs,
and the SDK's ExecHandle bounds its own client-side buffer.

The sandbox executes ONLY untrusted code (agent actions, eval commands); the
agent loop and model client never run inside it.
"""

from __future__ import annotations

import os
import platform
import secrets
import time
from typing import Any

from minisweagent.exceptions import Submitted
from minisweagent.utils.serialize import recursive_merge
from pydantic import BaseModel

try:
    import sky.sandbox as _sandbox  # standalone skypilot_sandbox_sdk wheel
except ImportError:
    try:
        import sky_sandbox as _sandbox  # top-level shim from the same wheel
    except ImportError:
        _sandbox = None  # tolerated for AGENT_LOCAL_EXEC-only hosts

# Escape hatch for the FULL multi-repo dataset. This example warms ONE pool per repo
# image, which is fast and reliable for a pinned repo subset (the default). Across the
# whole dataset, though, that's many pools competing for cluster sandbox capacity and
# many independent warm-ups — a reliability cliff. Set SANDBOX_NO_POOL=1 to skip pools
# entirely and create an on-demand ad-hoc sandbox per claim from the instance's own
# image (create(image=...)). Trade-off: a cold image pull on each claim instead of a
# warm-pool hit, so per-claim latency is higher. Rule of thumb: pools for a pinned
# subset, no-pool for the full dataset.
_NO_POOL = os.environ.get("SANDBOX_NO_POOL") == "1"


class SandboxEnvironmentConfig(BaseModel):
    cwd: str = "/workspace"
    """Working directory in which to execute commands."""
    env: dict[str, str] = {}
    """Environment variables to set for each exec."""
    timeout: int = 60
    """Per-action timeout in seconds (server-side exec timeout)."""


class SandboxEnvironment:
    """Executes each agent action as one stateless exec in a claimed sandbox.

    mini-swe's execution model (independent subprocess per action,
    environments/local.py) is semantically identical to sandbox exec, so the
    port is one method.
    """

    def __init__(self,
                 sb,
                 *,
                 config_class: type = SandboxEnvironmentConfig,
                 **kwargs) -> None:
        self.sb = sb
        self.config = config_class(**kwargs)

    def execute(self,
                action: dict,
                cwd: str = "",
                *,
                timeout: int | None = None) -> dict[str, Any]:
        """Run one bash action; mirrors DockerEnvironment.execute's output dict."""
        command = action.get("command", "")
        cwd = cwd or self.config.cwd
        timeout = timeout or self.config.timeout
        try:
            handle = self.sb.exec(
                "bash",
                "-c",
                command,
                workdir=cwd,
                timeout_seconds=float(
                    timeout),  # server-side kill: no orphaned commands
                env=self.config.env or None,
            )
            # client wait bounded slightly above the server kill so we always
            # collect the exit code the server assigns
            returncode = handle.wait(timeout=timeout + 30)
            # mini-swe merges stderr into stdout (local.py uses stderr=STDOUT);
            # the SDK keeps separate streams, so concatenate. Decode defensively:
            # agent commands run arbitrary code and can emit non-UTF-8 bytes.
            out_b, err_b = handle.stdout.read(), handle.stderr.read()
            output_text = ((out_b.decode("utf-8", "replace") if isinstance(
                out_b, bytes) else out_b) + (err_b.decode(
                    "utf-8", "replace") if isinstance(err_b, bytes) else err_b))
            output = {
                "output": output_text,
                "returncode": returncode,
                "exception_info": ""
            }
        except Exception as e:  # timeout / transport -> observation, not crash
            output = {
                "output": "",
                "returncode": -1,
                "exception_info": f"An error occurred while executing the command: {e}",
                "extra": {
                    "exception_type": type(e).__name__,
                    "exception": str(e)
                },
            }
        self._check_finished(output)
        return output

    def _check_finished(self, output: dict):
        """Raises Submitted if the output indicates task completion.

        Verbatim from environments/docker.py::_check_finished (the sentinel
        check is the Environment's job in upstream, not the agent's).
        """
        lines = output.get("output", "").lstrip().splitlines(keepends=True)
        if lines and lines[0].strip(
        ) == "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" and output[
                "returncode"] == 0:
            submission = "".join(lines[1:])
            raise Submitted({
                "role": "exit",
                "content": submission,
                "extra": {
                    "exit_status": "Submitted",
                    "submission": submission
                },
            })

    def get_template_vars(self, **kwargs) -> dict[str, Any]:
        # Host uname, mirroring upstream DockerEnvironment.get_template_vars.
        # The instance template renders it as <system_information>; trainer
        # pods and sandbox images are both linux, so this is accurate enough.
        return recursive_merge(self.config.model_dump(),
                               platform.uname()._asdict(), kwargs)

    def serialize(self) -> dict:
        return {
            "info": {
                "config": {
                    "environment": self.config.model_dump(mode="json"),
                    "environment_type": f"{self.__class__.__module__}.{self.__class__.__name__}",
                }
            }
        }

    def write_files(self, files: dict[str, str] | None) -> None:
        """Stage text files (path -> content; relative paths land under cwd).

        Sync (called via asyncio.to_thread); raises on failure -- staging
        errors are rollout aborts, not model observations.
        """
        for path, content in (files or {}).items():
            dst = path if path.startswith(
                "/") else f"{self.config.cwd.rstrip('/')}/{path}"
            parent = os.path.dirname(dst) or "/"
            rc = self.sb.exec("mkdir", "-p", parent).wait()
            if rc != 0:
                raise RuntimeError(f"failed to mkdir {parent} (exit {rc})")
            self.sb.write_text(content, dst)


def stage_files_locally(cwd: str, files: dict[str, str] | None) -> None:
    """Local-filesystem twin of SandboxEnvironment.write_files, for the
    AGENT_LOCAL_EXEC path (which uses upstream LocalEnvironment)."""
    for path, content in (files or {}).items():
        dst = path if os.path.isabs(path) else os.path.join(cwd, path)
        os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
        with open(dst, "w", encoding="utf-8") as f:
            f.write(content)


# --- pool / sandbox lifecycle helpers (used by generate.py and trainer setup) ---


async def claim_sandbox(
    pool: str,
    *,
    lifetime_sec: float | None = None,
    name: str | None = None,
    workdir: str | None = None,
    image: str | None = None,
):
    """Claim one sandbox for a rollout. Caller must terminate it.

    Warm-pool mode (default): claim a ready sandbox from ``pool``; if the pool has
    no ready box yet (slow/partial warm), fall back to an on-demand sandbox from
    ``image`` so the rollout still runs.
    No-pool mode (``SANDBOX_NO_POOL=1``): always create an on-demand ad-hoc sandbox
    from ``image`` — no pool needed, at the cost of a cold image pull per claim. Use
    it for the full multi-repo dataset, where one-pool-per-image doesn't scale.

    ``lifetime_sec`` sets the server-side auto-reap (``create(timeout=...)``),
    so a crashed trainer cannot leak sandboxes past that lifetime.
    ``workdir`` is created if given — generic pool images (python:*-slim)
    don't ship /workspace, and every action cd's into it (exec workdir= is a
    plain `cd`, rc=127 if missing; verified live 2026-07-14).
    """
    if _sandbox is None:
        raise RuntimeError(
            "sky.sandbox SDK not installed (skypilot_sandbox_sdk wheel)")
    name = name or f"r3-{secrets.token_hex(4)}"  # unique to avoid name reuse across rollouts
    if _NO_POOL:
        # On-demand ad-hoc sandbox from the instance's own image (bypasses pools).
        sb = await _sandbox.create.aio(name=name,
                                       image=image,
                                       cpus=1,
                                       memory_gb=2,
                                       timeout=lifetime_sec)
    else:
        try:
            sb = await _sandbox.create.aio(name=name,
                                           pool=pool,
                                           timeout=lifetime_sec)
        except Exception as e:  # noqa: BLE001
            # Pool claim failed — usually the pool hasn't warmed a ready box yet
            # (slow / partial warm; see ensure_pool). Fall back to an on-demand
            # sandbox from the repo image so the rollout still runs (cold image
            # pull instead of a warm hit). Re-raise only if we have no image.
            if image is None:
                raise
            print(
                f"[sandbox] pool {pool!r} claim failed ({e}); falling back to "
                f"on-demand sandbox {name!r}",
                flush=True)
            sb = await _sandbox.create.aio(name=name,
                                           image=image,
                                           cpus=1,
                                           memory_gb=2,
                                           timeout=lifetime_sec)
    if workdir:
        try:
            handle = await sb.exec.aio("mkdir", "-p", workdir)
            await handle.wait()
        except Exception:
            await terminate_sandbox(sb)
            raise
    return sb


async def terminate_sandbox(sb) -> None:
    """Best-effort terminate; never raises (used from finally blocks)."""
    try:
        await sb.terminate.aio()
    except Exception:
        pass


def ensure_pool(name: str,
                *,
                image: str,
                replicas: int,
                cpus: float = 1,
                memory_gb: float = 2) -> None:
    """Create the warm pool, tolerating one that already exists.

    Same pattern as sandbox_reward_server.py:196-220: create_pool, and on
    failure (already exists) make sure it is at least the requested size.
    Called once from trainer setup, not per rollout.
    """
    if _sandbox is None:
        raise RuntimeError(
            "sky.sandbox SDK not installed (skypilot_sandbox_sdk wheel)")
    # Time the create_pool call (greppable: "[pool-timing]"). This is the pool
    # REGISTRATION cost; the image-pull / replica-warm tail then shows up as the
    # cold-claim latency in sandbox_claim_sec p95/p99 (first claims wait for a
    # replica incl. pull). Both matter for the R2E per-repo-image caching work:
    # a per-image pool's first warm-up is dominated by image pull, so this is the
    # baseline we'll optimize (pre-pull / image locality).
    t0 = time.perf_counter()
    try:
        _sandbox.create_pool(name=name,
                             image=image,
                             cpus=cpus,
                             memory_gb=memory_gb,
                             replicas=replicas)
        dt = time.perf_counter() - t0
        print(
            f"[pool-timing] created {name!r} image={image} replicas={replicas} create_call={dt:.2f}s",
            flush=True)
        return
    except Exception as e:  # noqa: BLE001
        dt = time.perf_counter() - t0
        msg = str(e)
        # A 400 Bad Request means the create was REJECTED (e.g. name too long or
        # invalid) — not recoverable, and set_pool_size can't fix it. Fail loudly.
        if "400" in msg or "Bad Request" in msg:
            raise RuntimeError(f"create_pool({name!r}) rejected: {msg}") from e
        # Otherwise the pool either already exists OR didn't fully warm within the
        # SDK timeout (msg carries "last seen N/replicas"). NEITHER is fatal: the
        # pool exists and keeps warming, and any claim that can't get a ready box
        # falls back to an on-demand sandbox (see claim_sandbox). Log how far it
        # got and proceed rather than crashing the whole run on a slow/partial warm.
        print(
            f"[pool-timing] {name!r} not confirmed at full size ({msg}) in {dt:.2f}s; "
            f"ensuring size {replicas} and proceeding (overflow claims fall back on-demand)",
            flush=True)
        try:
            _sandbox.set_pool_size(name, replicas=replicas)
        except Exception as e2:  # noqa: BLE001
            print(
                f"[pool-timing] set_pool_size({name!r}) did not confirm full warm ({e2}); "
                f"proceeding on the partial pool",
                flush=True)


async def aclose() -> None:
    """Release the shared sandbox SDK session (process shutdown only)."""
    if _sandbox is None:
        return
    try:
        await _sandbox.aclose()
    except Exception:
        pass
