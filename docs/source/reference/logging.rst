.. _logging:

Usage Collection
=================

SkyPilot collects usage stats by default. This data will only be used by the SkyPilot team to improve its services and for research purpose.
We will **not** sell data or buy data about you.


What data is collected?
-----------------------

We collect non-sensitive data that helps us understand how SkyPilot is used. We will redact your ``setup``, ``run``, and ``env`` from the collected data.

In addition, SkyPilot uses `Scarf <https://scarf.sh>`__ to report an anonymous ping when a command is invoked on a client machine. The ping contains only the name of the invoked command and the SkyPilot version — no PII. Internal invocations (e.g. by SkyPilot controllers or the API server) and dry runs are not reported.

API server heartbeat
~~~~~~~~~~~~~~~~~~~~

In addition to the per-command data above, a SkyPilot API server sends a
periodic heartbeat every 10 minutes for as long as the server is running. The
heartbeat reports:

- ``total_gpus`` — installed GPU capacity, summed over every node in every
  Kubernetes and SSH context the server is allowed to use. This is capacity,
  not usage: an idle GPU node counts the same as a busy one. Clusters on cloud
  VMs have no node inventory to read and do not contribute. TPUs are excluded.
- ``gpus_by_type`` — the same total broken down by accelerator type, e.g.
  ``{"H100": 64, "A100:80GB": 16}``.
- ``infra_count`` — how many infrastructures of each kind the server is
  configured to use, e.g.
  ``{"kubernetes": 3, "ssh_node_pools": 1, "slurm": 0, "clouds": 2}``. These
  are counts only: no context names, cluster names, or cloud account
  identifiers are included. A kind whose count could not be determined is
  omitted rather than reported as zero.
- ``hostname``, ``server_hash`` (a stable per-installation identifier),
  ``sky_version``, and, when deployed via Helm, ``release_name`` and
  ``ingress_host``.

No cluster names, user names, task YAML, or cloud credentials are included.

Deployments that install a plugin registering a heartbeat data provider report
additional fields contributed by that plugin.

The heartbeat is sent by the API server process, not by the client, so
disabling it requires setting the environment variable on the server (see
below). It is not sent when the server is running as a jobs or serve
controller.

.. _usage-disable:

How to disable it
-----------------
To disable usage collection, set the ``SKYPILOT_DISABLE_USAGE_COLLECTION`` environment variable by :code:`export SKYPILOT_DISABLE_USAGE_COLLECTION=1`. This disables both the usage stats and the Scarf ping.

The Scarf ping alone can also be disabled by setting either of the industry-standard environment variables ``DO_NOT_TRACK=1`` or ``SCARF_NO_ANALYTICS=true``.

For the API server heartbeat, set it in the server's own environment; setting
it only on the client does not stop the heartbeat.


How does it work?
-----------------

When a SkyPilot CLI or entrypoint function is called, SkyPilot will do the following:

#. Check the environment variable ``SKYPILOT_DISABLE_USAGE_COLLECTION`` is set: 1 means disabled and 0 means enabled.

#. If the environment variable is not set or set to 0, it will collect information about the cluster and task resource requirements 

#. If the environment variable is set to 1, it will skip any message sending.
