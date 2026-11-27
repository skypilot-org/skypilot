.. _logging:

Usage Collection
=================

SkyPilot collects usage stats by default. This data will only be used by the SkyPilot team to improve its services and for research purpose.
We will **not** sell data or buy data about you.


What data is collected?
-----------------------

We collect non-sensitive data that helps us understand how SkyPilot is used. We will redact your ``setup``, ``run``, and ``env`` from the collected data.

In addition, SkyPilot uses `Scarf <https://scarf.sh>`__ to report an anonymous ping when a command is invoked on a client machine. The ping contains only the name of the invoked command and the SkyPilot version — no PII. Internal invocations (e.g. by SkyPilot controllers or the API server) and dry runs are not reported.

.. _usage-disable:

How to disable it
-----------------
To disable usage collection, set the ``SKYPILOT_DISABLE_USAGE_COLLECTION`` environment variable by :code:`export SKYPILOT_DISABLE_USAGE_COLLECTION=1`. This disables both the usage stats and the Scarf ping.

The Scarf ping alone can also be disabled by setting either of the industry-standard environment variables ``DO_NOT_TRACK=1`` or ``SCARF_NO_ANALYTICS=true``.


How does it work?
-----------------

When a SkyPilot CLI or entrypoint function is called, SkyPilot will do the following:

#. Check the environment variable ``SKYPILOT_DISABLE_USAGE_COLLECTION`` is set: 1 means disabled and 0 means enabled.

#. If the environment variable is not set or set to 0, it will collect information about the cluster and task resource requirements 

#. If the environment variable is set to 1, it will skip any message sending.
