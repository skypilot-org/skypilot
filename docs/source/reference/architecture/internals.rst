.. _architecture-internals:

SkyPilot Internal Architecture
===============================

SkyPilot state management
-------------------------

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

Internal Ray usage
------------------

.. note::
  
  SkyPilot uses Ray internally for cluster management. Understanding this architecture
  helps avoid conflicts when running Ray workloads on SkyPilot clusters.

SkyPilot leverages Ray for distributed cluster orchestration and management. 
The internal Ray cluster is isolated from user workloads through port separation:

**Port allocation:**

- ``6380``: SkyPilot's internal Ray cluster (avoiding Ray's default 6379)
- ``8266``: SkyPilot's Ray dashboard (avoiding Ray's default 8265)  
- ``/tmp/ray_skypilot``: Temporary directory for SkyPilot's Ray processes

**Configuration files:**

- ``~/.sky/ray_port.json``: Stores Ray port configuration
- ``~/.sky/python_path``: Python executable path for Ray commands
- ``~/.sky/ray_path``: Ray executable path

**Key implementation details:**

SkyPilot starts its Ray cluster when provisioning nodes:

- On the head node: ``ray start --head`` with port 6380 and custom resource configurations
- On worker nodes: ``ray start --address`` connecting to the head node's internal IP
- Ray version is set to ``2.9.3`` in ``SKY_REMOTE_RAY_VERSION``
- Runtime environment: ``skypilot-runtime`` conda environment

**Important considerations for users:**

- Never use ``ray.init(address="auto")`` - this connects to SkyPilot's internal cluster
- Always start user Ray clusters on the default port 6379
- Never run ``ray stop`` - this may disrupt SkyPilot operations
- To kill your Ray cluster, use `ray.shutdown() <https://docs.ray.io/en/latest/ray-core/api/doc/ray.shutdown.html>`_ in Python or kill the Ray processes directly:

  .. code-block:: bash

     # Kill specific Ray head process started on port 6379 (user's Ray cluster)
     pkill -f "ray start --head --port 6379"

For running Ray workloads on SkyPilot, refer to the :ref:`distributed jobs documentation <dist-jobs>`.
