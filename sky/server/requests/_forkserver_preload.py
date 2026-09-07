"""Modules preloaded into the multiprocessing ``forkserver``.

When the executor uses the ``forkserver`` start method, this module is imported
*once* in the long-lived fork server process (see
``multiprocessing.set_forkserver_preload``). Every executor worker is then
forked from that server, so it inherits the already-imported ``sky`` package
and plugin modules copy-on-write instead of re-importing them on each start.
This turns worker startup from several CPU-seconds into a near-free fork and
lets many workers share the imported pages in memory.

IMPORTANT — this module must stay free of process-global side effects, in
particular it must NOT open any database connection or socket, or start any
thread. The fork server is forked into every worker, so anything created here
(e.g. a SQLAlchemy engine with live connections, or a daemon thread holding a
lock) would be shared across / inherited by all workers and corrupt their
state. Two consequences:
- ``sky``'s database engines are created lazily on first query
  (``sky.utils.db.db_utils.get_engine``), so importing the package here is
  safe; do not trigger a query from this module.
- We only *import* plugin modules here; we do NOT call ``load_plugins`` (which
  also instantiates and installs plugins, and may start daemon threads / grab
  locks). Installation still happens per worker in ``executor_initializer``.
"""
import gc

# Heavy import shared by every executor worker. This is the dominant cost we
# want the fork server to pay once on behalf of all workers.
import sky  # noqa: F401  pylint: disable=unused-import

try:
    # Import (but do not install) the configured plugin modules, so a worker's
    # initializer (executor_initializer -> plugins.load_plugins) reuses the
    # already-imported modules instead of importing them afresh. Best-effort.
    from sky.server import plugins
    plugins.preload_plugin_modules()
except Exception:  # pylint: disable=broad-except
    pass

# Move everything imported above into a permanent GC generation so that
# reference-count churn on these long-lived objects does not dirty the shared
# copy-on-write pages after a fork, preserving the memory savings.
gc.freeze()
