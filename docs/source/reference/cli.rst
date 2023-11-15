.. _cli:

Command Line Interface
======================

Core CLI
---------

.. click:: sky.api.cli:launch
   :prog: sky launch
   :nested: full

.. click:: sky.api.cli:exec
   :prog: sky exec
   :nested: full

.. click:: sky.api.cli:stop
   :prog: sky stop
   :nested: full

.. click:: sky.api.cli:start
   :prog: sky start
   :nested: full

.. click:: sky.api.cli:down
   :prog: sky down
   :nested: full

.. click:: sky.api.cli:status
   :prog: sky status
   :nested: full

.. click:: sky.api.cli:autostop
   :prog: sky autostop
   :nested: full

Job Queue CLI
--------------

.. click:: sky.api.cli:queue
   :prog: sky queue
   :nested: full

.. click:: sky.api.cli:logs
   :prog: sky logs
   :nested: full

.. click:: sky.api.cli:cancel
   :prog: sky cancel
   :nested: full


Managed Spot Jobs CLI
---------------------------

.. click:: sky.api.cli:spot_launch
   :prog: sky spot launch
   :nested: full

.. click:: sky.api.cli:spot_queue
   :prog: sky spot queue
   :nested: full

.. click:: sky.api.cli:spot_cancel
   :prog: sky spot cancel
   :nested: full

.. click:: sky.api.cli:spot_logs
   :prog: sky spot logs
   :nested: full

Interactive Node CLI
-----------------------

.. click:: sky.api.cli:cpunode
   :prog: sky cpunode
   :nested: full

.. _sky-gpunode:
.. click:: sky.api.cli:gpunode
   :prog: sky gpunode
   :nested: full

.. click:: sky.api.cli:tpunode
   :prog: sky tpunode
   :nested: full


Storage CLI
------------

.. click:: sky.api.cli:storage_ls
   :prog: sky storage ls
   :nested: full

.. click:: sky.api.cli:storage_delete
   :prog: sky storage delete
   :nested: full

Utils: ``show-gpus``/``check``/``cost-report``
-------------------------------------------------


.. click:: sky.api.cli:show_gpus
   :prog: sky show-gpus
   :nested: full

.. click:: sky.api.cli:check
   :prog: sky check
   :nested: full

.. click:: sky.api.cli:cost_report
   :prog: sky cost-report
   :nested: full
