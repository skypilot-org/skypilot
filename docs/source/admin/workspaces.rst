.. _workspaces:

Workspaces: Isolating Teams
=======================================

**Workspaces** in SkyPilot allow you to isolate resources and configurations for
different teams or projects.

Example use cases:

- **Team Isolation**: Have different teams use different infra providers.
- **Project Separation**: Set different SkyPilot configurations for different projects.
- **Resource Tracking**: Track resource usage by team or project.


Defining workspaces
-------------------

Defining workspaces in configuration file
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Workspaces are defined in the :ref:`SkyPilot config file <config-yaml>` of API server, i.e., ``~/.sky/config.yaml`` on the API server host.

For example, assume:

- Your organization has two teams, team-a and team-b.
- SkyPilot API server is configured to use AWS and GCP.
- You want team-a to use both infra, and team-b to use GCP only with a different project ID.

The above is achieved by configuring the following section in the config file:

.. code-block:: yaml

   workspaces:
     team-a:
       # Use both AWS and GCP.
       gcp:
         project_id: project-id-for-team-a

     team-b:
       # Use GCP only with a different project ID.
       gcp:
         project_id: project-id-for-team-b
       aws:
         disabled: true

      team-c:
         private: true
         allowed_users:
         - alice@example.com
         - bob@example.com


.. dropdown:: Schema of the ``workspaces`` field:

   .. code-block:: yaml

      workspaces:
        # Workspace 'default' is created by SkyPilot and is used if no
        # workspaces are defined. Admins can optionally override settings for
        # this workspace.
        default: {}

        <workspace name>:
           <infra name>:  # aws, gcp, ...; ssh; kubernetes
             disabled: false  # Disable an infra for this workspace (default: false).

           # Certain infra providers support additional fields:
           ssh:
             disabled: false
             allowed_node_pools:
               - node-pool-1

           kubernetes:
             disabled: false
             allowed_contexts:
               - node-pool-1
               - node-pool-2

           gcp:
             disabled: false
             project_id: GCP project ID

           nebius:
             disabled: false
             tenant_id: Nebius tenant ID (tenant-xxxxxxxx)
             credentials_file_path: ~/.nebius/credentials-file-name.json

To apply the configuration, follow the following steps:

.. tab-set::

   .. tab-item:: Helm Deployment

      If you used :ref:`Helm Deployment <sky-api-server-helm-deploy-command>` to
      deploy a remote API server, write workspace configuration in a local
      SkyPilot config file, and run:

      .. code-block:: bash

         # RELEASE_NAME and NAMESPACE are the same as the ones used in the Helm
         # deployment.
         helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
            --namespace $NAMESPACE \
            --reuse-values \
            --set-file apiService.config=/your/path/to/config.yaml

      To change workspace configuration, update the config file and run the same command again. The API server will reload the new configuration automatically with no downtime. For more details, refer to :ref:`Setting the SkyPilot config in Helm Deployment <sky-api-server-config>`.

   .. tab-item:: VM Deployment or Local API Server

      If you used a :ref:`VM Deployment <sky-api-server-cloud-deploy>` for your
      API server or testing workspaces locally, edit the workspace configuration
      in the :ref:`SkyPilot config file <config-yaml>`, ``~/.sky/config.yaml``.
      The API server will automatically reload the configuration to apply the
      changes.

Defining workspaces in UI
~~~~~~~~~~~~~~~~~~~~~~~~~

Equivalently, you can also define workspaces in the UI.

The SkyPilot UI (``sky dashboard``) has a **Workspaces** page that shows all configured workspaces.

.. image:: ../images/workspaces/overview.png
   :alt: SkyPilot dashboard workspaces tab

This page shows all workspaces, and for each workspace its current clusters/jobs usage information and enabled infra choices.

To create a new workspace, click on the **Create New Workspace** button. Users can edit the workspace's definition in the UI.

To view or edit a workspace's definition, click on a workspace's **Edit** button.

.. image:: ../images/workspaces/edit.png
   :alt: SkyPilot dashboard workspaces edit



Setting the active workspace
----------------------------

Whenever a SkyPilot CLI or API is called, the workspace to use is picked up from the client side's ``active_workspace`` configuration.

For example, if your organization has a monorepo with multiple teams/projects:

.. code-block:: console

   /monorepo
   └── team-a
       └── .sky.yaml
       └── training.sky.yaml
   └── team-b
       └── .sky.yaml
       └── batch-infer.sky.yaml
   └── team-c
       └── # No .sky.yaml file; use the 'default' workspace.
       └── serve.sky.yaml
   └── ...

Then, ``/monorepo/team-a/.sky.yaml`` may set:

.. code-block:: yaml

   active_workspace: team-a

All SkyPilot CLI or API calls with ``/monorepo/team-a`` as the PWD will use the workspace ``team-a``.

.. code-block:: console

   $ cd /monorepo/team-a
   $ sky launch -c training training.sky.yaml

Similarly, ``/monorepo/team-b/.sky.yaml`` may set:

.. code-block:: yaml

   active_workspace: team-b

All SkyPilot CLI or API calls with ``/monorepo/team-b`` as the PWD will use the workspace ``team-b``.

For team-c, since it has no workspace set, all CLI or API calls there will use the ``default`` workspace.


.. tip::

   You can also set the workspace in the CLI using ``--config active_workspace=<workspace>``:

   .. code-block:: bash

      sky launch --config active_workspace=team-a ...

   See also :ref:`config-sources-and-overrides` for various ways to set configuration.


Workspaces in UI
----------------

Workspaces are shown in the **Workspaces** page in ``sky dashboard``.

In the **Clusters** and **Jobs** pages, all compute resources are tagged with the
workspace they belong to. Users can filter the view by workspace. Example:

.. image:: ../images/workspaces/resources.png
   :alt: SkyPilot dashboard workspaces resources


Private workspaces
------------------


Private workspaces are workspaces that are only accessible to a specific set of users.

To create a private workspace, set the ``private`` field to ``true`` in the workspace configuration.

.. code-block:: yaml

   workspaces:
     private-ws:
       private: true
       allowed_users:
         - alice@example.com
         - bob@example.com


Private workspaces can only be accessed by admins or the users in the ``allowed_users`` list. For
users who cannot access the workspace, they cannot see/access/operate on the workspace or the resources in the workspace.

.. note::

   The ``allowed_users`` field can be a list of user names or IDs. Note, if you
   have multiple users with the same name, you need to specify the user IDs instead.

User management
----------------

Currently, all users in an organization are allowed to use any configured
workspace. Users can flexibly switch to a different workspace as shown above.

SkyPilot supports using Okta, Google Workspace, or other SSO providers to
authenticate users. See :ref:`here <api-server-auth-proxy>`.
