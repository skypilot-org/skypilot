"""Modules preloaded into the multiprocessing ``forkserver``.

When the executor uses the ``forkserver`` start method, this module is imported
*once* in the long-lived fork server process (see
``multiprocessing.set_forkserver_preload``). Every executor worker is then
forked from that server, so it inherits the already-imported ``sky`` package
(and plugins) copy-on-write instead of paying a full ``import sky`` on each
start. This turns worker startup from several CPU-seconds into a near-free
fork and lets many workers share the imported pages in memory.

IMPORTANT — this module must stay free of process-global side effects, in
particular it must NOT open any database connection, socket, or start any
thread. The fork server is forked into every worker, so anything created here
(e.g. a SQLAlchemy engine with live connections) would be shared across all
workers and corrupt their state. ``sky``'s database engines are created lazily
on first query (``sky.utils.db.db_utils.get_engine``), so importing the package
here is safe; do not trigger a query from this module.
"""
import gc

# Heavy import shared by every executor worker. This is the dominant cost we
# want the fork server to pay once on behalf of all workers.
import sky  # noqa: F401  pylint: disable=unused-import

try:
    # Preload plugin modules too, so a worker's initializer
    # (executor_initializer -> plugins.load_plugins) is a no-op re-registration
    # rather than a fresh set of imports. Best-effort: if plugin loading is not
    # applicable or fails here, the worker still loads them in its initializer.
    from sky.server import plugins
    plugins.load_plugins(
        plugins.ExtensionContext(context=plugins.PluginContext.EXECUTOR))
except Exception:  # pylint: disable=broad-except
    pass

# Move everything imported above into a permanent GC generation so that
# reference-count churn on these long-lived objects does not dirty the shared
# copy-on-write pages after a fork, preserving the memory savings.
gc.freeze()
