.. _pythonapi:

Python SDK
==========

SkyPilot offers a Python SDK, which is used under the hood by the :ref:`CLI <cli>`.

Most SDK calls are **asynchronous and return a future** (``request ID``).

To wait and get the results:

* :ref:`sky.get(request_id) <sky-get>`: Wait for a request to finish, and get the results or exceptions.
* :ref:`sky.stream_and_get(request_id) <sky-stream-and-get>`: Stream the logs of a request, and get the results or exceptions.

To manage asynchronous requests:

* :ref:`sky.api_status() <sky-api-status>`: List all requests and their statuses.
* :ref:`sky.api_cancel(request_id) <sky-api-cancel>`: Cancel a request.

Refer to the ``Request Returns`` and ``Request Raises`` sections of each API for more details.

.. note::

  **Upgrading from v0.8 or older:** If you upgraded from a version equal to or older than 0.8.0 to any newer version, you need to update your program to adapt to the new
  :ref:`asynchronous execution model <async>`. See the :ref:`migration guide <migration-0.8.1>` for more details.


Clusters SDK
------------

``sky.launch``
~~~~~~~~~~~~~~

.. autofunction:: sky.launch
  :noindex:

``sky.stop``
~~~~~~~~~~~~~~

.. autofunction:: sky.stop
  :noindex:

``sky.start``
~~~~~~~~~~~~~~

.. autofunction:: sky.start
  :noindex:

``sky.down``
~~~~~~~~~~~~~~

.. autofunction:: sky.down
  :noindex:

``sky.status``
~~~~~~~~~~~~~~

.. autofunction:: sky.status
  :noindex:

``sky.autostop``
~~~~~~~~~~~~~~~~

.. autofunction:: sky.autostop
  :noindex:

``sky.endpoints``
~~~~~~~~~~~~~~~~~

.. autofunction:: sky.endpoints
  :noindex:


Jobs SDK
--------

Cluster jobs SDK
~~~~~~~~~~~~~~~~

``sky.exec``
^^^^^^^^^^^^^^

.. autofunction:: sky.exec
  :noindex:


``sky.queue``
^^^^^^^^^^^^^^

.. autofunction:: sky.queue
  :noindex:

``sky.job_status``
^^^^^^^^^^^^^^^^^^

.. autofunction:: sky.job_status
  :noindex:


``sky.tail_logs``
^^^^^^^^^^^^^^^^^

.. autofunction:: sky.tail_logs
  :noindex:


``sky.download_logs``
^^^^^^^^^^^^^^^^^^^^^

.. autofunction:: sky.download_logs
  :noindex:

``sky.cancel``
^^^^^^^^^^^^^^^

.. autofunction:: sky.cancel
  :noindex:


Managed jobs SDK
~~~~~~~~~~~~~~~~~~~~~~~

``sky.jobs.launch``
^^^^^^^^^^^^^^^^^^^

.. autofunction:: sky.jobs.launch
  :noindex:

``sky.jobs.queue_v2``
^^^^^^^^^^^^^^^^^^^^^

.. autofunction:: sky.jobs.queue_v2
  :noindex:

``sky.jobs.queue``
^^^^^^^^^^^^^^^^^^^

.. autofunction:: sky.jobs.queue
  :noindex:

``sky.jobs.cancel``
^^^^^^^^^^^^^^^^^^^

.. autofunction:: sky.jobs.cancel
  :noindex:


``sky.jobs.tail_logs``
^^^^^^^^^^^^^^^^^^^^^^

.. autofunction:: sky.jobs.tail_logs
  :noindex:


Volumes SDK
------------

``sky.volumes.ls``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. autofunction:: sky.volumes.ls
  :noindex:

``sky.volumes.apply``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. autofunction:: sky.volumes.apply
  :noindex:

``sky.volumes.delete``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. autofunction:: sky.volumes.delete
  :noindex:


Serving SDK
-----------------


``sky.serve.up``
~~~~~~~~~~~~~~~~

.. autofunction:: sky.serve.up
  :noindex:


``sky.serve.update``
~~~~~~~~~~~~~~~~~~~~

.. autofunction:: sky.serve.update
  :noindex:


``sky.serve.down``
~~~~~~~~~~~~~~~~~~

.. autofunction:: sky.serve.down
  :noindex:


``sky.serve.terminate_replica``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. autofunction:: sky.serve.terminate_replica
  :noindex:


``sky.serve.status``
~~~~~~~~~~~~~~~~~~~~

.. autofunction:: sky.serve.status
  :noindex:


``sky.serve.tail_logs``
~~~~~~~~~~~~~~~~~~~~~~~

.. autofunction:: sky.serve.tail_logs
  :noindex:


.. _sky-dag-ref:

Task
-----------------

.. autoclass:: sky.Task
  :members:
  :exclude-members: validate, validate_run, validate_name, expand_and_validate_file_mounts, expand_and_validate_workdir, is_controller_task, get_required_cloud_features, estimate_runtime, get_cloud_to_remote_file_mounts, get_inputs_cloud, get_local_to_remote_file_mounts, set_time_estimator, sync_storage_mounts, to_yaml_config

  .. automethod:: __init__


Resources
-----------------

.. autoclass:: sky.Resources
  :members: copy

  .. automethod:: __init__


Enums
-----------------

.. autoclass:: sky.ClusterStatus
  :members:

.. autoclass:: sky.JobStatus
  :members:

.. autoclass:: sky.StatusRefreshMode
  :members:


.. _ref-api-server-sdk:

API server SDK
-----------------

.. _sky-get:

``sky.get``
~~~~~~~~~~~

.. autofunction:: sky.get
  :noindex:

.. _sky-stream-and-get:

``sky.stream_and_get``
~~~~~~~~~~~~~~~~~~~~~~

.. autofunction:: sky.stream_and_get
  :noindex:

``sky.api_status``
~~~~~~~~~~~~~~~~~~

.. autofunction:: sky.api_status
  :noindex:

``sky.api_cancel``
~~~~~~~~~~~~~~~~~~

.. autofunction:: sky.api_cancel
  :noindex:

``sky.api_info``
~~~~~~~~~~~~~~~~

.. autofunction:: sky.api_info
  :noindex:

``sky.api_start``
~~~~~~~~~~~~~~~~~

.. autofunction:: sky.api_start
  :noindex:

``sky.api_stop``
~~~~~~~~~~~~~~~~~

.. autofunction:: sky.api_stop
  :noindex:

``sky.api_server_logs``
~~~~~~~~~~~~~~~~~~~~~~~

.. autofunction:: sky.api_server_logs
  :noindex:
