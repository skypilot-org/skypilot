.. _cloud-auth:

Cloud Accounts and Permissions
==============================


.. _cloud-permissions:

Minimal cloud permissions
-------------------------

SkyPilot is designed to minimize cloud administration and setup steps. It should work out of the box for most users who own the root (admin) account of their clouds, if they follow the steps in :ref:`Installation <installation>` section.

However, in some cases, the cloud administrator may want to **limit the permissions** of the account used by SkyPilot. This is especially true if the user is using a shared cloud account. The following sections describe the permissions required by SkyPilot for each cloud provider.

.. note::

    Setting up minimal permissions is completely **optional** and is not needed to get started with SkyPilot. These steps are for users who want to make SkyPilot use non-default, restricted permissions.


Table of contents
-----------------

.. toctree::

    aws
    gcp
    vsphere
    kubernetes
