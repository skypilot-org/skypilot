"""Modal cloud adaptor."""

import os
import threading
from typing import Any

from sky import sky_logging
from sky.adaptors import common

logger = sky_logging.init_logger(__name__)

_IMPORT_ERROR_MESSAGE = ('Failed to import dependencies for Modal. '
                         'Try running: pip install "skypilot[modal]"')
_UNSET = object()


class _ModalImport(common.LazyImport):
    """LazyImport of `modal` that follows changes to the Modal config file.

    modal reads ~/.modal.toml (or $MODAL_CONFIG_PATH) once, at import, and
    caches an API client built from it. A long-lived process -- e.g. an API
    server request worker -- would otherwise never see `modal token new` or
    a token uploaded after it first touched modal. The first access reloads
    modal's config, since modal may have been imported before the file
    existed; each later access re-stats the file and, when it changed,
    reloads the config and drops the cached client.
    """

    def __init__(self):
        super().__init__('modal', import_error_message=_IMPORT_ERROR_MESSAGE)
        self._config_signature: Any = _UNSET
        self._config_version = 0
        self._config_lock = threading.Lock()

    def __getattr__(self, name: str) -> Any:
        self._refresh_config(self.load_module())
        return super().__getattr__(name)

    def config_version(self) -> int:
        """Bumped whenever a changed Modal config is reloaded.

        Use it as a cache key for anything derived from the credentials,
        such as the workspace identity.
        """
        self._refresh_config(self.load_module())
        return self._config_version

    def _refresh_config(self, module: Any) -> None:
        config = getattr(module, 'config', None)
        if config is None:
            return
        try:
            # Follows a symlinked config to the file it points at.
            stat = os.stat(config.user_config_path)
            signature = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
        except (OSError, AttributeError):
            signature = None
        if signature == self._config_signature:
            return
        with self._config_lock:
            if signature == self._config_signature:
                return
            # Reload on the first access too. modal reads the file at import,
            # and other code in this process (a plugin, say) may have imported
            # modal directly before another process wrote the file.
            first_access = self._config_signature is _UNSET
            # modal has no public reload API. If these internals change, keep
            # the config modal already loaded rather than break every access.
            try:
                # pylint: disable=protected-access
                config._user_config = config._read_user_config()
                config._profile = (os.environ.get('MODAL_PROFILE') or
                                   config._config_active_profile())
                module.Client.set_env_client(None)
            except Exception:  # pylint: disable=broad-except
                # Leave the signature unchanged so the next access retries.
                logger.debug('Failed to reload the Modal config.',
                             exc_info=True)
                return
            if not first_access:
                # Nothing was derived from the config before the first access.
                self._config_version += 1
            # Publish the signature last: other threads skip the lock once it
            # matches, so they must not see it before the reload is done.
            self._config_signature = signature


modal = _ModalImport()
