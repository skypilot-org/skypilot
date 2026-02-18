.. _slurm-pricing:

Configuring Pricing
===================

By default, Slurm virtual instance types report a cost of ``$0.00`` in
``sky launch``, ``sky status``, and ``sky gpus list``.

To display meaningful cost estimates, add per-vCPU, per-GB, and
per-accelerator hourly rates in your ``~/.sky/config.yaml``:

.. code-block:: yaml

    slurm:
      pricing:
        cpu: 0.04        # $/vCPU/hr
        memory: 0.01     # $/GB/hr
        accelerators:
          V100: 2.50     # $/accelerator/hr
          A100: 3.50

All fields are optional; unset fields contribute ``$0.00`` to the total.

Per-cluster and per-partition overrides
---------------------------------------

You can set different pricing for each Slurm cluster and partition using
``cluster_configs``. Pricing at each level is deep-merged with the parent
level — only the keys you specify are overridden, and unmentioned accelerators
are inherited.

The merge order is::

    cloud-level  <  cluster-level  <  partition-level

.. code-block:: yaml

    slurm:
      # Cloud-level defaults
      pricing:
        cpu: 0.04
        memory: 0.01
        accelerators:
          V100: 2.50
          A100: 3.50

      cluster_configs:
        mycluster1:
          # Override cpu rate for this cluster; memory and accelerators
          # are inherited from the cloud-level pricing above.
          pricing:
            cpu: 0.06
            accelerators:
              A100: 4.00   # Override A100; V100 inherited

        mycluster2:
          pricing:
            cpu: 0.03
          partition_configs:
            gpu-partition:
              # Override accelerator rate for this partition; cpu and
              # memory are inherited from the cluster-level pricing.
              pricing:
                accelerators:
                  H100: 5.00

See :ref:`slurm.pricing <config-yaml-slurm-pricing>` and
:ref:`slurm.cluster_configs <config-yaml-slurm-cluster-configs>` in the
:ref:`advanced configuration reference <config-yaml>` for full details on all
supported fields.
