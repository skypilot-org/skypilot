.. _logging:

Usage Collection
=================

Sky collects usage stats by default. This data will only be used by the Sky team to improve its services and for research purpose.
We will **not** sell data or buy data about you.


What data is collected?
-----------------------

We collect non-sensitive data that helps us understand how Sky is used. We will redact your :code:`setup`` and :code:`run`` codes from the collected data.

.. _usage-disable:

How to disable it
-----------------
To disable usage collection, set the ``SKY_DISABLE_USAGE_COLLECTION`` environment variable by :code:`export SKY_DISABLE_USAGE_COLLECTION=1`.


How does it work?
-----------------

When a Sky CLI or entrypoint function is called, Sky will do the following:

#. Check the environment variable ``SKY_DISABLE_USAGE_COLLECTION`` is set: 1 means disabled and 0 means enabled.

#. If the environment variable is not set or set to 0, it will collect the cluster and task resources and send them to a hosted server.

#. If the environment variable is set to 1, it will skip any message sending.
