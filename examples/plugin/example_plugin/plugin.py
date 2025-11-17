"""Example plugin for SkyPilot API server."""
import fastapi

from sky.server import plugins


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
        extension_context.app.include_router(self.build_router())


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
        extension_context.app.include_router(self.build_router())
