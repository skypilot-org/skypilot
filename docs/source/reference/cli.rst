.. _cli:

Command Line Interface
======================

Core CLI
---------

.. click:: sky.cli:launch
   :prog: sky launch
   :nested: full

.. click:: sky.cli:exec
   :prog: sky exec
   :nested: full

.. click:: sky.cli:stop
   :prog: sky stop
   :nested: full

.. click:: sky.cli:start
   :prog: sky start
   :nested: full

.. click:: sky.cli:down
   :prog: sky down
   :nested: full

.. click:: sky.cli:status
   :prog: sky status
   :nested: full

.. click:: sky.cli:autostop
   :prog: sky autostop
   :nested: full

Job Queue CLI
--------------

.. click:: sky.cli:queue
   :prog: sky queue
   :nested: full

.. click:: sky.cli:logs
   :prog: sky logs
   :nested: full

.. click:: sky.cli:cancel
   :prog: sky cancel
   :nested: full


Managed Spot Jobs CLI
---------------------------

.. click:: sky.cli:spot_launch
   :prog: sky spot launch
   :nested: full

.. click:: sky.cli:spot_queue
   :prog: sky spot queue
   :nested: full

.. click:: sky.cli:spot_cancel
   :prog: sky spot cancel
   :nested: full

.. click:: sky.cli:spot_logs
   :prog: sky spot logs
   :nested: full

Storage CLI
------------

.. click:: sky.cli:storage_ls
   :prog: sky storage ls
   :nested: full

.. click:: sky.cli:storage_delete
   :prog: sky storage delete
   :nested: full

Utils: ``show-gpus``/``check``/``cost-report``
-------------------------------------------------


.. click:: sky.cli:show_gpus
   :prog: sky show-gpus
   :nested: full

.. click:: sky.cli:check
   :prog: sky check
   :nested: full

.. click:: sky.cli:cost_report
   :prog: sky cost-report
   :nested: full
