.. _cloud-permissions-gcp:

GCP
=============


Generally, the administrator can choose among three "levels" of permissions, from the most permissive and least setup effort, to the least permissive and more setup effort:

* Default: no setup, give users Owner-level permissions (i.e., you do not need to follow the instructions in this section)
* :ref:`Medium <gcp-medium-permissions>`: easy setup, with a medium set of permissions
* :ref:`Minimal <gcp-minimal-permissions>`: more setup, with the minimal set of permissions

.. _gcp-medium-permissions:

Medium Permissions
-----------------------

The easiest way to grant permissions to a user access your GCP project without the ``Owner`` role is to add the following roles to the user principals:

.. code-block:: yaml

  roles/browser
  roles/compute.admin
  roles/iam.serviceAccountAdmin
  roles/iam.serviceAccountUser
  roles/serviceusage.serviceUsageConsumer
  roles/storage.admin

Optionally, to use TPUs, add the following role:

.. code-block:: yaml

  roles/tpu.admin

You can grant those accesses via GCP's `IAM & Admin console <https://console.cloud.google.com/iam-admin/iam>`__.

.. _gcp-minimal-permissions:

Minimal Permissions
-----------------------

The :ref:`Medium Permissions <gcp-medium-permissions>` assigns admin permissions for some GCP services to the user.  If you would like to grant finer-grained and more minimal permissions to your users in your organization / project, you can create a custom role by following the steps below:

User
~~~~~~~~~~~~

1. Go to GCP's `IAM & Admin console <https://console.cloud.google.com/iam-admin/roles>`__ and click on **Create Role**.

.. image:: ../../images/screenshots/gcp/create-role.png
    :width: 80%
    :align: center
    :alt: GCP Create Role

2. Give the role a descriptive name, such as ``minimal-skypilot-role``.
3. Click **Add Permissions** and search for the following permissions and add them to the role:

.. code-block:: text

    compute.disks.create
    compute.disks.list
    compute.firewalls.create
    compute.firewalls.delete
    compute.firewalls.get
    compute.instances.create 
    compute.instances.delete
    compute.instances.get
    compute.instances.list
    compute.instances.setLabels
    compute.instances.setMetadata
    compute.instances.setServiceAccount
    compute.instances.start
    compute.instances.stop
    compute.networks.get
    compute.networks.list
    compute.networks.getEffectiveFirewalls
    compute.globalOperations.get
    compute.subnetworks.use
    compute.subnetworks.list
    compute.subnetworks.useExternalIp
    compute.projects.get
    compute.zoneOperations.get
    iam.roles.get
    iam.serviceAccounts.actAs
    iam.serviceAccounts.get
    serviceusage.services.enable
    serviceusage.services.list
    serviceusage.services.use
    resourcemanager.projects.get
    resourcemanager.projects.getIamPolicy

4. **Optional**: If the user needs to access GCS buckets, you can additionally add the following permissions:

.. code-block:: text

    storage.buckets.create
    storage.buckets.get
    storage.buckets.delete
    storage.objects.create
    storage.objects.delete
    storage.objects.get
    storage.objects.list

5. **Optional**: If the user needs to access TPU VMs, you can additionally add the following permissions (the following may not be exhaustive, please file an issue if you find any missing permissions):

.. code-block:: text

    tpu.nodes.create
    tpu.nodes.delete
    tpu.nodes.list
    tpu.nodes.get
    tpu.nodes.update
    tpu.operations.get

6. **Optional**: To enable ``sky launch --clone-disk-from``, you need to have the following permissions for the role as well:

.. code-block:: text

    compute.disks.useReadOnly
    compute.images.create
    compute.images.get
    compute.images.delete

7. Click **Create** to create the role.
8. Go back to the "IAM" tab and click on **GRANT ACCESS**.
9. Fill in the email address of the user in the “Add principals” section, and select ``minimal-skypilot-role`` in the “Assign roles” section. Click **Save**.


.. image:: ../../images/screenshots/gcp/create-iam.png
    :width: 80%
    :align: center
    :alt: GCP Grant Access

10. The user should receive an invitation to the project and should be able to setup SkyPilot by following the instructions in :ref:`Installation <installation-gcp>`.

.. note::

    The user created with the above minimal permissions will not be able to create service accounts to be assigned to SkyPilot instances. 
    
    The admin needs to follow the :ref:`instruction below <gcp-service-account-creation>` to create a service account to be shared by all users in the project.


.. _gcp-service-account-creation:

Service Account
~~~~~~~~~~~~~~~~~~~
.. note::

    If you already have an service account under "Service Accounts" tab with the email starting with ``skypilot-v1@``, it is likely created by SkyPilot automatically, and you can skip this section.

1. Click the "Service Accounts" tab in the "IAM & Admin" console, and click on the **CREATE SERVICE ACCOUNT**.

.. image:: ../../images/screenshots/gcp/create-service-account.png
    :width: 80%
    :align: center
    :alt: GCP Create Service Account

2. Set the service account id to ``skypilot-v1`` and click **CREATE AND CONTINUE**.

.. image:: ../../images/screenshots/gcp/service-account-name.png
    :width: 60%
    :align: center
    :alt: Set Service Account Name

3. Select the ``minimal-skypilot-role`` (or the name you set) created in the last section and click on **DONE**.

.. image:: ../../images/screenshots/gcp/service-account-grant-role.png
    :width: 60%
    :align: center
    :alt: Set Service Account Role

