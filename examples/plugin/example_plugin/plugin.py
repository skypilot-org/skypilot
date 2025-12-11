"""Example plugin for SkyPilot API server."""
import functools
import logging
import os
import threading
import time

import fastapi
import filelock
import starlette.middleware.base

from sky import check as sky_check
from sky.provision import common
from sky.server import plugins

logger = logging.getLogger(__name__)


class ExamplePlugin(plugins.BasePlugin):
    """Example plugin for SkyPilot API server."""

    def build_router(self) -> fastapi.APIRouter:
        router = fastapi.APIRouter(prefix='/plugins/example',
                                   tags=['example plugin'])

        @router.get('/')
        async def get_example():
            return {'message': 'Hello from example_plugin'}

        return router

    def install(self, extension_context: plugins.ExtensionContext):
        if extension_context.app:
            extension_context.app.include_router(self.build_router())
        extension_context.register_rbac_rule(path='/plugins/example/*',
                                             method='GET',
                                             description='Example plugin',
                                             role='user')


class ExampleParameterizedPlugin(plugins.BasePlugin):
    """Example plugin with parameters for SkyPilot API server."""

    def __init__(self, message: str):
        self._message = message

    def build_router(self) -> fastapi.APIRouter:
        router = fastapi.APIRouter(prefix='/plugins/example_parameterized',
                                   tags=['example_parameterized plugin'])

        @router.get('/')
        async def get_example():
            return {'message': self._message}

        return router

    def install(self, extension_context: plugins.ExtensionContext):
        if extension_context.app:
            extension_context.app.include_router(self.build_router())


class ExampleMiddleware(starlette.middleware.base.BaseHTTPMiddleware):
    """Example middleware for SkyPilot API server."""

    async def dispatch(self, request: fastapi.Request, call_next):
        client = request.client
        logger.info(f'Audit request {request.method} {request.url.path} '
                    f'from {client.host if client else "unknown"}')
        return await call_next(request)


class ExampleMiddlewarePlugin(plugins.BasePlugin):
    """Example plugin that adds a middleware for SkyPilot API server."""

    def install(self, extension_context: plugins.ExtensionContext):
        if extension_context.app:
            extension_context.app.add_middleware(ExampleMiddleware)


class ExampleBackgroundTaskPlugin(plugins.BasePlugin):
    """Example plugin that runs a background task on the API server."""

    def install(self, extension_context: plugins.ExtensionContext):

        lock_path = os.path.expanduser('~/.sky/plugins/check_context_task.lock')
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)

        def check_contexts():
            lock = filelock.FileLock(lock_path)
            try:
                with lock.acquire(blocking=False):
                    while True:
                        try:
                            sky_check.check(clouds=['kubernetes'])
                        except Exception as e:
                            logger.error('Error checking contexts: %s', e)
                        time.sleep(60)
            except filelock.Timeout:
                # Other process is already running the task, skip it.
                pass

        threading.Thread(target=check_contexts, daemon=True).start()


class ExamplePatchPlugin(plugins.BasePlugin):
    """Example plugin that patches the SkyPilot API server."""

    def install(self, extension_context: plugins.ExtensionContext):
        # pylint: disable=import-outside-toplevel
        from sky.provision.kubernetes import instance

        original_run_instances = instance.run_instances

        @functools.wraps(original_run_instances)
        def patched_run_instances(
                region: str, cluster_name: str, cluster_name_on_cloud: str,
                config: common.ProvisionConfig) -> common.ProvisionRecord:
            result = original_run_instances(region, cluster_name,
                                            cluster_name_on_cloud, config)
            logger.info('Post action after running instances')
            return result

        instance.run_instances = patched_run_instances
