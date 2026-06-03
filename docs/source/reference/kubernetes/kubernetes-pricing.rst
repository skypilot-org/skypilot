.. _kubernetes-pricing:

Configuring Pricing
===================

By default, Kubernetes virtual instance types report a cost of ``$0.00`` in
``sky launch``, ``sky status``, and ``sky gpus list`` because SkyPilot has no
way to know the true cost of on-prem or self-managed clusters.

To display meaningful cost estimates, add hourly rates in your
``~/.sky/config.yaml``:

.. code-block:: yaml

    kubernetes:
      pricing:
        cpu: 0.05        # $/vCPU/hr  (CPU-only instances)
        memory: 0.01     # $/GB/hr    (CPU-only instances)
        accelerators:
          A100: 3.50     # $/accelerator/hr (all-in, includes cpu/memory)
          H100: 5.00

Pricing uses two mutually exclusive tiers: **CPU-only instances** (no
accelerator) use the ``cpu`` and ``memory`` rates, while **accelerator
instances** use only the per-accelerator rate (an all-in price that includes
cpu and memory). All fields are optional; unset fields contribute ``$0.00``.

Per-context overrides
---------------------

When using :ref:`multiple Kubernetes clusters <multi-kubernetes>`, you can set
different pricing for each context using
:ref:`context_configs <config-yaml-kubernetes-context-configs>`.
Context-level pricing is deep-merged with the cloud-level default — only the
keys you specify are overridden, and unmentioned accelerators are inherited:

.. code-block:: yaml

    kubernetes:
      pricing:
        cpu: 0.05
        memory: 0.01
        accelerators:
          A100: 3.50
          H100: 5.00
      context_configs:
        on-prem-cluster:
          pricing:
            # Overrides only the cpu rate; memory and accelerators
            # are inherited from the cloud-level pricing above.
            cpu: 0.08

See :ref:`kubernetes.pricing <config-yaml-kubernetes-pricing>` in the
:ref:`advanced configuration reference <config-yaml>` for full details on all
supported fields.
