.. _cli:

Command Line Interface
======================

Cluster CLI
-----------

.. _sky-launch:
.. click:: sky.client.cli.command:launch
   :prog: sky launch
   :nested: full

.. _sky-stop:
.. click:: sky.client.cli.command:stop
   :prog: sky stop
   :nested: full

.. _sky-start:
.. click:: sky.client.cli.command:start
   :prog: sky start
   :nested: full

.. _sky-down:
.. click:: sky.client.cli.command:down
   :prog: sky down
   :nested: full

.. _sky-status:
.. click:: sky.client.cli.command:status
   :prog: sky status
   :nested: full

.. _sky-autostop:
.. click:: sky.client.cli.command:autostop
   :prog: sky autostop
   :nested: full


Jobs CLI
--------

Cluster jobs CLI
~~~~~~~~~~~~~~~~

.. _sky-exec:
.. click:: sky.client.cli.command:exec
   :prog: sky exec
   :nested: full

.. _sky-queue:
.. click:: sky.client.cli.command:queue
   :prog: sky queue
   :nested: full

.. _sky-cancel:
.. click:: sky.client.cli.command:cancel
   :prog: sky cancel
   :nested: full

.. _sky-logs:
.. click:: sky.client.cli.command:logs
   :prog: sky logs
   :nested: full

Managed jobs CLI
~~~~~~~~~~~~~~~~~

.. _sky-job-launch:
.. click:: sky.client.cli.command:jobs_launch
   :prog: sky jobs launch
   :nested: full

.. _sky-job-queue:
.. click:: sky.client.cli.command:jobs_queue
   :prog: sky jobs queue
   :nested: full

.. _sky-job-cancel:
.. click:: sky.client.cli.command:jobs_cancel
   :prog: sky jobs cancel
   :nested: full

.. _sky-job-logs:
.. click:: sky.client.cli.command:jobs_logs
   :prog: sky jobs logs
   :nested: full

Serving CLI
-------------

.. click:: sky.client.cli.command:serve_up
   :prog: sky serve up
   :nested: full

.. click:: sky.client.cli.command:serve_down
   :prog: sky serve down
   :nested: full

.. click:: sky.client.cli.command:serve_status
   :prog: sky serve status
   :nested: full

.. click:: sky.client.cli.command:serve_logs
   :prog: sky serve logs
   :nested: full

.. click:: sky.client.cli.command:serve_update
   :prog: sky serve update
   :nested: full


Storage CLI
------------

.. _sky-storage-ls:
.. click:: sky.client.cli.command:storage_ls
   :prog: sky storage ls
   :nested: full

.. _sky-storage-delete:
.. click:: sky.client.cli.command:storage_delete
   :prog: sky storage delete
   :nested: full


Volumes CLI
------------

.. _sky-volumes-ls:
.. click:: sky.client.cli.command:volumes_ls
   :prog: sky volumes ls
   :nested: full

.. _sky-volumes-apply:
.. click:: sky.client.cli.command:volumes_apply
   :prog: sky volumes apply
   :nested: full

.. _sky-volumes-delete:
.. click:: sky.client.cli.command:volumes_delete
   :prog: sky volumes delete
   :nested: full


.. _sky-api-cli:

API request CLI
---------------

.. _sky-api-login:
.. click:: sky.client.cli.command:api_login
   :prog: sky api login
   :nested: full

.. _sky-api-info:
.. click:: sky.client.cli.command:api_info
   :prog: sky api info
   :nested: full

.. _sky-api-logs:
.. click:: sky.client.cli.command:api_logs
   :prog: sky api logs
   :nested: full

.. _sky-api-status:
.. click:: sky.client.cli.command:api_status
   :prog: sky api status
   :nested: full

.. _sky-api-cancel:
.. click:: sky.client.cli.command:api_cancel
   :prog: sky api cancel
   :nested: full

Admin CLI
~~~~~~~~~

.. click:: sky.client.cli.command:api_stop
   :prog: sky api stop
   :nested: full

.. click:: sky.client.cli.command:api_start
   :prog: sky api start
   :nested: full


Utils: ``show-gpus``/``check``/``cost-report``
-------------------------------------------------

.. _sky-show-gpus:
.. click:: sky.client.cli.command:show_gpus
   :prog: sky show-gpus
   :nested: full

.. _sky-check:
.. click:: sky.client.cli.command:check
   :prog: sky check
   :nested: full

.. click:: sky.client.cli.command:cost_report
   :prog: sky cost-report
   :nested: full
