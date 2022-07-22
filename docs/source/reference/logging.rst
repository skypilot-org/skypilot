.. _logging:

Usage Collection
=================

SkyPilot collects usage stats by default. This data will only be used by the SkyPilot team to improve its services and for research purpose.
We will **not** sell data or buy data about you.


What data is collected?
-----------------------

We collect non-sensitive data that helps us understand how SkyPilot is used. We will redact your ``setup``, ``run``, and ``env`` from the collected data.

.. _usage-disable:

How to disable it
-----------------
To disable usage collection, set the ``SKYPILOT_DISABLE_USAGE_COLLECTION`` environment variable by :code:`export SKYPILOT_DISABLE_USAGE_COLLECTION=1`.


How does it work?
-----------------

When a SkyPilot CLI or entrypoint function is called, SkyPilot will do the following:

#. Check the environment variable ``SKYPILOT_DISABLE_USAGE_COLLECTION`` is set: 1 means disabled and 0 means enabled.

#. If the environment variable is not set or set to 0, it will collect information about the cluster and task resource requirements 

#. If the environment variable is set to 1, it will skip any message sending.
