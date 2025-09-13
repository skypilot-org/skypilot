.. _cloud-auth:

Cloud Accounts and Permissions
==============================

Some providers in SkyPilot may offer identity and permission-related configuration options. These are **optional** and mostly targeted at cloud admins. **For most users, SkyPilot should work out of the box** with the permissions you have configured locally during :ref:`installation <cloud-account-setup>`.

The subpages here document cloud configuration details that admins may want to use to limit access, set up advanced features, etc.

.. _cloud-permissions:

Minimal cloud permissions
-------------------------

SkyPilot is designed to minimize cloud administration and setup steps. It should work out of the box for most users who own the root (admin) account of their clouds, if they follow the steps in :ref:`Installation <installation>` section.

However, in some cases, the cloud administrator may want to **limit the permissions** of the account used by SkyPilot. This is especially true if the user is using a shared cloud account. The docs linked below describe the permissions required by SkyPilot for each cloud provider.

.. note::

    Setting up minimal permissions is completely **optional** and is not needed to get started with SkyPilot. The instructions in these docs are only needed for users who want to make SkyPilot use non-default, restricted permissions.


Table of contents
-----------------

.. toctree::

    aws
    gcp
    nebius
    vsphere
    kubernetes
