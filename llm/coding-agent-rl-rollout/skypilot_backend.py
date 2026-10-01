"""A SkyPilot-sandbox backend for vime's ``Sandbox`` protocol.

vime's coding-agent RL example is written against a tiny async ``Sandbox``
protocol::

    @runtime_checkable
    class Sandbox(Protocol):
        sandbox_id: str
        async def __aenter__(self) -> "Sandbox": ...
        async def __aexit__(self, exc_type, exc, tb) -> None: ...
        async def exec(self, cmd, *, user="root", env=None,
                       timeout=120, check=False) -> tuple[int, str, str]: ...
        async def write_file(self, sandbox_path, content, *, user="root") -> None: ...
        async def read_file(self, sandbox_path, *, user="root") -> str: ...

Any backend that satisfies this protocol is a drop-in. ``SkyPilotSandbox`` below
is such a backend, implemented on the public SkyPilot sandbox SDK
(``sky.sandbox``).

Four impedance mismatches between the two interfaces are handled here:

1. vime ``exec`` takes a shell string; the SDK ``exec`` takes argv tokens and
   runs them directly via the kubernetes exec stream (no shell). We wrap the
   string in ``bash -lc``.
2. vime ``exec`` has a ``user`` kwarg; the SDK has none. Non-root commands are
   run via ``runuser -u <user>``.
3. vime ``exec`` has a per-call ``env``; the SDK has none. We inline the env as
   literal ``export K=V;`` statements ahead of the command (literal values, so
   nothing gets clobbered when dropping to another user).
4. vime ``write_file`` accepts a host ``Path``; we stream it via the SDK's
   ``write_bytes`` and ``chown`` to the target user afterwards.

Requires the SkyPilot sandbox feature (the ``sky.sandbox`` SDK) and a SkyPilot
API server reachable by the SDK.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import secrets as _secrets_mod
import shlex
from typing import Dict, List, Optional, Tuple, Union

try:
    import sky.sandbox as sandbox  # public SDK: ``import sky; sky.sandbox...``
except ImportError:
    # The Sandbox SDK also ships a top-level ``sky_sandbox`` shim.
    import sky_sandbox as sandbox

FileContent = Union[str, bytes, Path]
ExecResult = Tuple[int, str, str]


def _gen_name(prefix: str = 'vime') -> str:
    return f'{prefix}-{_secrets_mod.token_hex(4)}'


class SkyPilotSandbox:
    """vime ``Sandbox`` protocol over a SkyPilot sandbox.

    Args:
        image: Ad-hoc container image (cold start, no warm pool). Mutually
            exclusive with ``pool``. Defaults to ``python:3.12`` when neither is
            given.
        pool: Warm pool to launch into instead of an ad-hoc image.
        name: Sandbox name. Auto-generated if omitted.
        cpus / memory_gb: Resource shape for an ad-hoc (``image``) launch.
        env: Plain env vars baked into the pod at create time.
        context_name / namespace: Kubernetes target. ``None`` uses the current
            kubeconfig context / its bound namespace.
        timeout: Server-side auto-reap max lifetime (seconds). Recommended for
            rollouts so an abandoned sandbox cleans itself up.
        ready_timeout: How long ``__aenter__`` waits for the pod to become
            exec-able before giving up.
    """

    def __init__(
        self,
        image: Optional[str] = None,
        *,
        pool: Optional[str] = None,
        name: Optional[str] = None,
        cpus: Optional[float] = None,
        memory_gb: Optional[float] = None,
        env: Optional[Dict[str, str]] = None,
        context_name: Optional[str] = None,
        namespace: Optional[str] = None,
        timeout: Optional[float] = None,
        ready_timeout: float = 300.0,
    ) -> None:
        if image and pool:
            raise ValueError('Pass either image or pool, not both.')
        if not image and not pool:
            image = 'python:3.12'
        self._image = image
        self._pool = pool
        self._name = name or _gen_name()
        self._cpus = cpus
        self._memory_gb = memory_gb
        self._env = dict(env or {})
        self._context_name = context_name
        self._namespace = namespace
        self._timeout = timeout
        self._ready_timeout = ready_timeout
        # vime protocol attribute; set for real after start().
        self.sandbox_id: str = self._name
        self._sb: Optional[sandbox.Sandbox] = None

    # --- lifecycle --------------------------------------------------------

    async def start(self) -> 'SkyPilotSandbox':
        kwargs: Dict[str, object] = dict(
            name=self._name,
            env=self._env or None,
            context_name=self._context_name,
            namespace=self._namespace,
            timeout=self._timeout,
        )
        if self._image:
            kwargs.update(image=self._image,
                          cpus=self._cpus,
                          memory_gb=self._memory_gb)
        else:
            kwargs['pool'] = self._pool
        self._sb = await sandbox.create.aio(**kwargs)
        self.sandbox_id = self._sb.name
        if self._sb.status == 'FAILED':
            raise RuntimeError(f'Sandbox {self._sb.name} failed to create: '
                               f'{self._sb.error_message}')
        await self._wait_ready()
        return self

    async def _wait_ready(self) -> None:
        """Poll a trivial exec until the pod accepts commands."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + self._ready_timeout
        last_err: Optional[BaseException] = None
        while loop.time() < deadline:
            try:
                res = await self._sb.exec.aio('true', timeout_seconds=15)
                if int(res.get('exit_code', -1)) == 0:
                    return
            except Exception as e:  # noqa: BLE001 - pod not ready yet
                last_err = e
            await asyncio.sleep(3)
        raise TimeoutError(
            f'Sandbox {self.sandbox_id} not exec-able within '
            f'{self._ready_timeout:.0f}s; last error: {last_err}')

    async def __aenter__(self) -> 'SkyPilotSandbox':
        return await self.start()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._sb is not None:
            try:
                await self._sb.terminate.aio(wait=False)
            except Exception:  # noqa: BLE001 - best-effort teardown
                pass

    # --- protocol ---------------------------------------------------------

    async def exec(
        self,
        cmd: str,
        *,
        user: str = 'root',
        env: Optional[Dict[str, str]] = None,
        timeout: int = 120,
        check: bool = False,
    ) -> ExecResult:
        exports = ''
        if env:
            exports = ''.join(
                f'export {k}={shlex.quote(str(v))}; ' for k, v in env.items())
        payload = exports + cmd
        if user and user != 'root':
            argv = ['runuser', '-u', user, '--', 'bash', '-lc', payload]
        else:
            argv = ['bash', '-lc', payload]
        assert self._sb is not None, 'sandbox not started'
        res = await self._sb.exec.aio(*argv, timeout_seconds=timeout)
        ec = int(res.get('exit_code', -1))
        out = res.get('stdout', '') or ''
        err = res.get('stderr', '') or ''
        if check and ec != 0:
            raise RuntimeError(f'exec failed (exit {ec}) for command: {cmd}\n'
                               f'--- stdout ---\n{out}\n--- stderr ---\n{err}')
        return ec, out, err

    async def write_file(
        self,
        sandbox_path: str,
        content: FileContent,
        *,
        user: str = 'root',
    ) -> None:
        assert self._sb is not None, 'sandbox not started'
        # Ensure the parent dir exists (write streams a single file).
        parent = os.path.dirname(sandbox_path.rstrip('/'))
        if parent:
            await self.exec(f'mkdir -p {shlex.quote(parent)}', user='root')
        if isinstance(content, Path):
            await self._sb.write_bytes.aio(content.read_bytes(), sandbox_path)
        elif isinstance(content, (bytes, bytearray)):
            await self._sb.write_bytes.aio(bytes(content), sandbox_path)
        else:
            await self._sb.write_text.aio(str(content), sandbox_path)
        if user and user != 'root':
            await self.exec(
                f'chown {shlex.quote(user)}:{shlex.quote(user)} '
                f'{shlex.quote(sandbox_path)}',
                user='root',
            )

    async def read_file(self, sandbox_path: str, *, user: str = 'root') -> str:
        assert self._sb is not None, 'sandbox not started'
        # The SDK reads server-side as root, so `user` does not change the read;
        # it is part of the protocol signature.
        del user
        return await self._sb.read_text.aio(sandbox_path)
