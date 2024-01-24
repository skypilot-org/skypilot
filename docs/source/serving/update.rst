.. _update:

Update
===========

SkyServe supports rolling update for your services.

Use ``sky serve update`` to update an existing service:

.. code-block:: console

    $ sky serve update service-name new_service.yaml

SkyServe will launch new replicas described by ``new_service.yaml``. When the number of new replicas reaches the minimum number of replicas (``min_replicas``), SkyServe will scale down old replicas to save cost. SkyServe allows users to update ``replica_policy`` parameters, such as ``target_qps_per_replica``. SkyServe also allows users to update ``resources`` parameters, such as ``cpu`` and ``memory``.  SkyServe, by default, does not mix traffic from old and new replicas. 

:code:`sky serve status` will highlight the latest service version and each replica's version. 