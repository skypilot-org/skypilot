.. _cloud-vm:

Using Cloud VMs
=====================

SkyPilot supports both elastic (on-demand, spot) and reserved cloud instances (virtual machines, or VMs).

Elastic VMs
-----------

SkyPilot supports launching elastic cloud instances on all major cloud providers.
Both on-demand and spot instances are supported.

Get started with :ref:`quickstart`.  See :ref:`concept-cloud-vms` for an overview.

Reserved VMs
------------

For reserved instances that are a set of long-running, SSH-accessible nodes, see
:ref:`existing-machines` to bring them into SkyPilot as an infra choice.

For reserved instances that require cloud-specific settings to use (e.g., AWS
Capacity Reservations and Capacity Blocks; GCP reservations; GCP DWS), see
:ref:`reservation`.


.. toctree::
   :hidden:
   :maxdepth: 1

   ../cloud-setup/quota
