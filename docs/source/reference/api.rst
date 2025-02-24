.. _pythonapi:

Python API
=================

SkyPilot offers a programmatic API in Python, which is used under the hood by the :ref:`CLI <cli>`.

Most of the SDK functions are **asynchronous and return a future** (``request ID``). To wait and get the results:

* :ref:`sky.get(request_id) <sky-get>`: wait and get the results or exceptions of the request.
* :ref:`sky.stream_and_get(request_id) <sky-stream-and-get>`: stream the logs and get the results or exceptions of the request.
* :ref:`sky.api_status() <sky-api-status>`: find all requests and their statuses.
* :ref:`sky.api_cancel(request_id) <sky-api-cancel>`: cancel a request.

Please refer to the ``Request Returns`` and ``Request Raises`` sections for more details of each SDK function's return values and exceptions.

.. note::

  The Python API contains more experimental functions/classes than the CLI. That
  said, it has been used to develop several Python libraries by users.

  For questions or request for support, please reach out to the development team.
  Your feedback is much appreciated in evolving this API!


Cluster API
-----------

sky.launch
~~~~~~~~~~

.. autofunction:: sky.launch

sky.exec
~~~~~~~~

.. autofunction:: sky.exec

sky.stop
~~~~~~~~~

.. autofunction:: sky.stop

sky.start
~~~~~~~~~~~~~

.. autofunction:: sky.start

sky.down
~~~~~~~~~

.. autofunction:: sky.down

sky.status
~~~~~~~~~~~~~

.. autofunction:: sky.status

sky.autostop
~~~~~~~~~~~~~

.. autofunction:: sky.autostop


sky.queue
~~~~~~~~~~

.. autofunction:: sky.queue

sky.job_status
~~~~~~~~~~~~~~~~

.. autofunction:: sky.job_status


sky.tail_logs
~~~~~~~~~~~~~~~~~

.. autofunction:: sky.tail_logs


sky.download_logs
~~~~~~~~~~~~~~~~~~~~~

.. autofunction:: sky.download_logs

sky.cancel
~~~~~~~~~~~

.. autofunction:: sky.cancel


Managed (Spot) Jobs API
-----------------------

sky.jobs.launch
~~~~~~~~~~~~~~~~~

.. autofunction:: sky.jobs.launch

sky.jobs.queue
~~~~~~~~~~~~~~~

.. autofunction:: sky.jobs.queue

sky.jobs.cancel
~~~~~~~~~~~~~~~~~

.. autofunction:: sky.jobs.cancel


sky.jobs_tail_logs
~~~~~~~~~~~~~~~~~~

.. autofunction:: sky.jobs.tail_logs


SkyServe API
-----------------


sky.serve.up
~~~~~~~~~~~~~

.. autofunction:: sky.serve.up


sky.serve.update
~~~~~~~~~~~~~~~~~

.. autofunction:: sky.serve.update


sky.serve.down
~~~~~~~~~~~~~~~

.. autofunction:: sky.serve.down


sky.serve.terminate_replica
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. autofunction:: sky.serve.terminate_replica


sky.serve.status
~~~~~~~~~~~~~~~~~

.. autofunction:: sky.serve.status


sky.serve.tail_logs
~~~~~~~~~~~~~~~~~~~~~

.. autofunction:: sky.serve.tail_logs


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


API Server APIs
-----------------

.. _sky-get:

sky.get
~~~~~~~

.. autofunction:: sky.get

.. _sky-stream-and-get:

sky.stream_and_get
~~~~~~~~~~~~~~~~~~~

.. autofunction:: sky.stream_and_get

sky.api_status
~~~~~~~~~~~~~~~

.. autofunction:: sky.api_status

sky.api_cancel
~~~~~~~~~~~~~~~

.. autofunction:: sky.api_cancel

sky.api_info
~~~~~~~~~~~~~~~

.. autofunction:: sky.api_info

sky.api_start
~~~~~~~~~~~~~~~

.. autofunction:: sky.api_start

sky.api_stop
~~~~~~~~~~~~~~~

.. autofunction:: sky.api_stop

sky.api_server_logs
~~~~~~~~~~~~~~~~~~~~~

.. autofunction:: sky.api_server_logs
