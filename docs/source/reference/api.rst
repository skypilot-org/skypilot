.. _pythonapi:

Python API
=================

SkyPilot offers a programmatic API in Python, which is used under the hood by the :ref:`CLI <cli>`.

.. note::

  The Python API contains more experimental functions/classes than the CLI. That
  said, it has been used to develop several Python libraries by users.

  For questions or request for support, please reach out to the development team.
  Your feedback is much appreciated in evolving this API!


Core API
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


Job Queue API
-----------------

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


Managed Spot Jobs API
-----------------------

sky.spot_launch
~~~~~~~~~~~~~~~~~

.. autofunction:: sky.spot_launch

sky.spot_queue
~~~~~~~~~~~~~~~

.. autofunction:: sky.spot_queue

sky.spot_cancel
~~~~~~~~~~~~~~~~~

.. autofunction:: sky.spot_cancel


sky.spot_tail_logs
~~~~~~~~~~~~~~~~~~

.. autofunction:: sky.spot_tail_logs

.. _sky-dag-ref:

Task
-----------------

.. autoclass:: sky.Task
  :members:
  :exclude-members: estimate_runtime, get_cloud_to_remote_file_mounts, get_inputs_cloud, get_local_to_remote_file_mounts, set_time_estimator, sync_storage_mounts, to_yaml_config

  .. automethod:: __init__


Resources
-----------------

.. autoclass:: sky.Resources
  
  .. automethod:: __init__
