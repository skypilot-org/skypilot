.. _benchmark-yaml:

YAML Configuration
==================

The resources to benchmark can be configured in the SkyPilot YAML interface.
Below we provide an example:

.. code-block:: yaml

    # Only shows `resources` as other fields do not change.
    resources:
        cloud: gcp  # Works as a default value for `cloud`.

        # Added only for SkyPilot Benchmark.
        candidates:
        - {accelerators: A100}
        - {accelerators: V100, instance_type: n1-highmem-16}
        - {accelerators: T4, cloud: aws}  # Overrides `cloud` to `aws`.

For SkyPilot Benchmark, ``candidates`` is newly added under the ``resources`` field.
``candidates`` is the list of dictionaries that configure the resources to benchmark.
Any subfield of ``resources`` (``accelerators``, ``instance_type``, etc.) can be re-defined in the dictionaries.
Subfields defined outside ``candidates`` (e.g. ``cloud`` in this example) are used as default values and are overriden by those defined in the dictionaries.
Thus, the above example can be interpreted as follows:

.. code-block:: yaml

    # Configuration of the first candidate.
    resources:
        cloud: gcp
        accelerators: A100

    # Configuration of the second candidate.
    resources:
        cloud: gcp
        accelerators: V100
        instance_type: n1-highmem-16

    # Configuration of the third candidate.
    resources:
        cloud: aws
        accelerators: T4

.. note::

    Currently, SkyPilot Benchmark does not support on-prem jobs and managed spot jobs.
    While you can set ``use_spot: True`` to benchmark spot VMs, automatic recovery will not be provided when preemption occurs.
