"""Plugin for smoke test."""
import fastapi

from sky.server import plugins


class TestPlugin(plugins.BasePlugin):
    """Plugin for smoke test."""

    def build_router(self) -> fastapi.APIRouter:
        router = fastapi.APIRouter(prefix='/plugins/smoke_test',
                                   tags=['smoke test'])

        @router.get('/')
        async def get():
            return {'message': 'smoke test plugin'}

        return router

    def install(self, extension_context: plugins.ExtensionContext):
        if extension_context.app:
            extension_context.app.include_router(self.build_router())
