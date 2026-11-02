"""API server plugin for the dashboard e2e suite. Never load it in production.

It does two things:

1. Turns off the background refresh daemons, so rows a test writes straight
   into the DB (clusters, volumes, managed jobs) are not reconciled against
   infra that does not exist on the test machine. The suite checks the
   dashboard, not cloud reconciliation.

2. Serves *server twists*: a request carrying ``X-E2E-Twist: <id>`` gets its
   JSON body rewritten before the real handler sees it. A twist only affects
   the test that asked for it, so twisted and plain tests share one server and
   run in parallel.

Twists are registered with ``register_body_twist``. Modules that register
them are named in ``E2E_SERVER_TWIST_MODULES`` (comma separated) and imported
when the plugin installs, so a suite adds its own twists without changing
this file.
"""
import importlib
import json
import os
from typing import Any, Callable, Dict, Tuple

import starlette.middleware.base
import starlette.requests

from sky import sky_logging
from sky.server import daemons
from sky.server import plugins

logger = sky_logging.init_logger(__name__)

TWIST_HEADER = 'x-e2e-twist'
TWIST_MODULES_ENV = 'E2E_SERVER_TWIST_MODULES'

_SKIPPED_DAEMONS = {
    'skypilot-status-refresh-daemon',
    'skypilot-volume-status-refresh-daemon',
    'sky-serve-status-refresh-daemon',
    'pool-status-refresh-daemon',
}

BodyRewrite = Callable[[Dict[str, Any]], Dict[str, Any]]
# twist id -> (request path suffix, body rewrite)
_BODY_TWISTS: Dict[str, Tuple[str, BodyRewrite]] = {}


def register_body_twist(twist_id: str, path_suffix: str,
                        rewrite: BodyRewrite) -> None:
    """Rewrite the JSON body of requests to ``*path_suffix`` that carry
    ``X-E2E-Twist: <twist_id>``."""
    assert twist_id not in _BODY_TWISTS, f'duplicate server twist {twist_id}'
    _BODY_TWISTS[twist_id] = (path_suffix, rewrite)


def _skip() -> bool:
    return True


class _TwistMiddleware(starlette.middleware.base.BaseHTTPMiddleware):
    """Applies the server twist a request names in its header."""

    async def dispatch(self, request: starlette.requests.Request,
                       call_next: Callable) -> Any:
        twist_id = request.headers.get(TWIST_HEADER)
        twist = _BODY_TWISTS.get(twist_id or '')
        if twist is not None and request.url.path.endswith(twist[0]):
            body = json.loads((await request.body()) or b'{}')
            # BaseHTTPMiddleware hands the cached body to the handler.
            request._body = json.dumps(twist[1](body)).encode()  # pylint: disable=protected-access
            logger.info(f'e2e twist {twist_id} applied to {request.url.path}')
        return await call_next(request)


class E2ETestPlugin(plugins.BasePlugin):
    """Disables refresh daemons and serves header-scoped server twists."""

    @property
    def name(self) -> str:
        return 'E2ETestPlugin'

    @property
    def hidden_from_display(self) -> bool:
        return True

    def install(self, extension_context: plugins.ExtensionContext):
        for daemon in daemons.INTERNAL_REQUEST_DAEMONS:
            if daemon.id in _SKIPPED_DAEMONS:
                daemon.should_skip = _skip
        for module in filter(None,
                             os.environ.get(TWIST_MODULES_ENV, '').split(',')):
            importlib.import_module(module.strip())
        if extension_context.app is not None:
            extension_context.app.add_middleware(_TwistMiddleware)
