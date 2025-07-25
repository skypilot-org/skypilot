.. _infrastructure-state:

SkyPilot State Management
=========================

This document describes how SkyPilot stores and generates internal states.

All SkyPilot internal data stores in `~/.sky/` directory:

- SQLite databases for states (if external database is not configured)
- API server logs
- Synchronization lock files
- SkyPilot catalog
- etc.

.. note::
  **External database, if configured**
  
  Users can optionally configure SkyPilot to use a PostgreSQL database to persists its state.
  See :ref:`API server database configuration <config-yaml-db>` for more details on how to configure an external database.

  If configured, SkyPilot uses the external database to store states instead of the SQLite databases in ``~/.sky/`` directory. All the other internal data is emphemeral, and fine to discard during upgrade.



Databases
---------

SkyPilot uses multiple databases to store its state.

- ``~/.sky/state.[db|db-shm|db-wal]``: SQLite database for storing user, cluster and storage state.
- ``~/.sky/spot_jobs.[db|db-shm|db-wal]``: SQLite database for storing managed job state.
- ``~/.sky/api_server/requests.[db|db-shm|db-wal]``: SQLite database for storing API server request state. **When an API server (replica) is stopped, the request records for the API server (replica) are automatically deleted.**

As mentioned above, the databases can be supported by a PostgreSQL backend if configured.
Configuring an external database allows the state to be persisted even if the local filesystem is lost (e.g. the API server pod is terminated)

Logs
----

Logs for the API server are stored in ``~/.sky/api_server/server.log`` file.

Logs for individual clusters / jobs are stored in ``~/sky_logs/`` directory.

Configuration
-------------

Configuration for SkyPilot API server is stored in ``~/.sky/config.yaml`` file.

SkyPilot can be configured to use an external database to store its configuration.
In this case, the configuration file at ``~/.sky/config.yaml`` can only contain the following field:

- ``db``: the database connection string.

See :ref:`API server database configuration <config-yaml-db>` for more details on how to configure an external database.

File / directory uploads
------------------------

When a user uploads a file or directory to a cluster with `workdir` or `file_mounts`, the SkyPilot client will automatically upload the files to a remote API server at directory: ``~/.sky/api_server/clients/<client-hash>/file_mounts``, as an intermediate store.
