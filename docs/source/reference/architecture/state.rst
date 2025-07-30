.. _architecture-state:

SkyPilot state management
=========================

All SkyPilot internal data is stored in the ``~/.sky/`` directory of the API server (either your local machine or a remote server):

- SQLite databases for states (if external database is not configured)
- API server logs
- Synchronization lock files
- SkyPilot catalog
- etc.

.. note::
  **External database, if configured**
  
  Users can optionally configure SkyPilot to use a PostgreSQL database to persists its state.
  See :ref:`API server database configuration <config-yaml-db>` for more details on how to configure an external database.

  If configured, SkyPilot uses the external database to store states instead of the SQLite databases in ``~/.sky/`` directory.
  All the other internal data is emphemeral, and is not critical to the operation of SkyPilot API server.

Configuration
-------------

Configuration for the SkyPilot API server is stored in the ``~/.sky/config.yaml`` file.

SkyPilot can be configured to use an external database to store its configuration.
In this case, the configuration file at ``~/.sky/config.yaml`` can only contain the following field:

- ``db``: the database connection string.

Configuring an external database allows the configuration to be persisted
even if the local filesystem is lost (e.g. the API server pod is terminated, potentially during an upgrade).

File / directory uploads
------------------------

When a user uploads a file or directory to a cluster with ``workdir`` or ``file_mounts``,
the SkyPilot client automatically uploads the files to a remote API server at directory:

``~/.sky/api_server/clients/<client-hash>/file_mounts``

as an intermediate store.
