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
~~~~~~~~

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

.. _sky-dag-ref:

Task and DAG
-----------------


sky.Task
~~~~~~~~~

.. autoclass:: sky.Task
    :members:

sky.Dag
~~~~~~~~~

.. autoclass:: sky.Dag
    :members:
