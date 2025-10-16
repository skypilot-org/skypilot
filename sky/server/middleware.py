"""Middleware configuration for FastAPI applications."""

import os

import fastapi
from fastapi.middleware import cors

from sky.server import metrics
from sky.server.auth import oauth2_proxy
from sky.skylet import constants


def apply_middleware(app: fastapi.FastAPI,
                     include_metrics: bool = True,
                     include_auth: bool = True,
                     include_cors: bool = True) -> None:
    """Apply all standard middleware to a FastAPI application.

    This function applies the standard middleware stack used by SkyPilot API
    server applications. The middleware is applied in a specific order to
    ensure proper functionality of authentication, RBAC, and other features.

    Args:
        app: The FastAPI application to configure.
        include_metrics: Whether to include Prometheus metrics middleware.
        include_auth: Whether to include authentication middleware.
        include_cors: Whether to include CORS middleware.
    """
    # Import here to avoid circular imports
    # pylint: disable=import-outside-toplevel
    from sky.server import server as server_module

    # Middleware wraps in the order defined here. E.g., given
    #   app.add_middleware(Middleware1)
    #   app.add_middleware(Middleware2)
    #   app.add_middleware(Middleware3)
    # The effect will be like:
    #   Middleware3(Middleware2(Middleware1(request)))
    # If MiddlewareN does something like print(n); call_next(); print(n),
    # you'll get:
    #   3; 2; 1; <request>; 1; 2; 3
    # Use environment variable to make the metrics middleware optional.
    if include_metrics and os.environ.get(
            constants.ENV_VAR_SERVER_METRICS_ENABLED):
        app.add_middleware(metrics.PrometheusMiddleware)

    app.add_middleware(server_module.APIVersionMiddleware)
    app.add_middleware(server_module.RBACMiddleware)
    app.add_middleware(server_module.InternalDashboardPrefixMiddleware)
    app.add_middleware(server_module.GracefulShutdownMiddleware)
    app.add_middleware(server_module.PathCleanMiddleware)
    app.add_middleware(server_module.CacheControlStaticMiddleware)

    if include_cors:
        app.add_middleware(
            cors.CORSMiddleware,
            # TODO(zhwu): in production deployment, we should restrict the
            # allowed origins to the domains that are allowed to access the
            # API server.
            allow_origins=['*'],  # Specify the correct domains for production
            allow_credentials=True,
            allow_methods=['*'],
            allow_headers=['*'],
            # TODO(syang): remove X-Request-ID when v0.10.0 is released.
            expose_headers=['X-Request-ID', 'X-Skypilot-Request-ID'])

    if include_auth:
        # The order of all the authentication-related middleware is important.
        # RBACMiddleware must precede all the auth middleware, so it can access
        # request.state.auth_user.
        app.add_middleware(server_module.RBACMiddleware)
        # Authentication based on oauth2-proxy.
        app.add_middleware(oauth2_proxy.OAuth2ProxyMiddleware)
        # AuthProxyMiddleware should precede BasicAuthMiddleware and
        # BearerTokenMiddleware, since it should be skipped if either of those
        # set the auth user.
        app.add_middleware(server_module.AuthProxyMiddleware)
        enable_basic_auth = os.environ.get(constants.ENV_VAR_ENABLE_BASIC_AUTH,
                                           'false')
        if str(enable_basic_auth).lower() == 'true':
            app.add_middleware(server_module.BasicAuthMiddleware)
        # Bearer token middleware should always be present to handle service
        # account authentication
        # authentication
        app.add_middleware(server_module.BearerTokenMiddleware)
        # InitializeRequestAuthUserMiddleware must be the last added middleware
        # so that request.state.auth_user is always set, but can be overridden
        # by the auth middleware above.
        # middleware above.
        app.add_middleware(server_module.InitializeRequestAuthUserMiddleware)

    app.add_middleware(server_module.RequestIDMiddleware)
