.. _cli:

Command Line Interface
======================

Cluster CLI
-----------

.. _sky-launch:
.. click:: sky.client.cli:launch
   :prog: sky launch
   :nested: full

.. _sky-stop:
.. click:: sky.client.cli:stop
   :prog: sky stop
   :nested: full

.. _sky-start:
.. click:: sky.client.cli:start
   :prog: sky start
   :nested: full

.. _sky-down:
.. click:: sky.client.cli:down
   :prog: sky down
   :nested: full

.. _sky-status:
.. click:: sky.client.cli:status
   :prog: sky status
   :nested: full

.. _sky-autostop:
.. click:: sky.client.cli:autostop
   :prog: sky autostop
   :nested: full


Jobs CLI
--------

Cluster jobs CLI
~~~~~~~~~~~~~~~~

.. _sky-exec:
.. click:: sky.client.cli:exec
   :prog: sky exec
   :nested: full

.. _sky-queue:
.. click:: sky.client.cli:queue
   :prog: sky queue
   :nested: full

.. _sky-cancel:
.. click:: sky.client.cli:cancel
   :prog: sky cancel
   :nested: full

.. _sky-logs:
.. click:: sky.client.cli:logs
   :prog: sky logs
   :nested: full

Managed jobs CLI
~~~~~~~~~~~~~~~~~

.. _sky-job-launch:
.. click:: sky.client.cli:jobs_launch
   :prog: sky jobs launch
   :nested: full

.. _sky-job-queue:
.. click:: sky.client.cli:jobs_queue
   :prog: sky jobs queue
   :nested: full

.. _sky-job-cancel:
.. click:: sky.client.cli:jobs_cancel
   :prog: sky jobs cancel
   :nested: full

.. _sky-job-logs:
.. click:: sky.client.cli:jobs_logs
   :prog: sky jobs logs
   :nested: full

Serving CLI
-------------

.. click:: sky.client.cli:serve_up
   :prog: sky serve up
   :nested: full

.. click:: sky.client.cli:serve_down
   :prog: sky serve down
   :nested: full

.. click:: sky.client.cli:serve_status
   :prog: sky serve status
   :nested: full

.. click:: sky.client.cli:serve_logs
   :prog: sky serve logs
   :nested: full

.. click:: sky.client.cli:serve_update
   :prog: sky serve update
   :nested: full


Storage CLI
------------

.. _sky-storage-ls:
.. click:: sky.client.cli:storage_ls
   :prog: sky storage ls
   :nested: full

.. _sky-storage-delete:
.. click:: sky.client.cli:storage_delete
   :prog: sky storage delete
   :nested: full


.. _sky-api-cli:

API request CLI
---------------

.. _sky-api-login:
.. click:: sky.client.cli:api_login
   :prog: sky api login
   :nested: full

.. _sky-api-info:
.. click:: sky.client.cli:api_info
   :prog: sky api info
   :nested: full

.. _sky-api-logs:
.. click:: sky.client.cli:api_logs
   :prog: sky api logs
   :nested: full

.. _sky-api-status:
.. click:: sky.client.cli:api_status
   :prog: sky api status
   :nested: full

.. _sky-api-cancel:
.. click:: sky.client.cli:api_cancel
   :prog: sky api cancel
   :nested: full

Admin CLI
~~~~~~~~~

.. click:: sky.client.cli:api_stop
   :prog: sky api stop
   :nested: full

.. click:: sky.client.cli:api_start
   :prog: sky api start
   :nested: full


Utils: ``show-gpus``/``check``/``cost-report``
-------------------------------------------------

.. _sky-show-gpus:
.. click:: sky.client.cli:show_gpus
   :prog: sky show-gpus
   :nested: full

.. _sky-check:
.. click:: sky.client.cli:check
   :prog: sky check
   :nested: full

.. click:: sky.client.cli:cost_report
   :prog: sky cost-report
   :nested: full
