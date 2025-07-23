.. _deploy-api-server-basic-auth:

Enable Basic Auth in the SkyPilot API Server
=============================================

When a SkyPilot API server is :ref:`deployed using a Helm chart <sky-api-server-helm-deploy-command>` following the default instructions, basic auth is enabled in the ingress controller, which does not support RBAC.

If you want to support RBAC with basic auth enabled, you can following the steps below to enable basic auth in the API server.

Deploy in Helm
~~~~~~~~~~~~~~

Set up the environment variables for your API server deployment. ``NAMESPACE`` and ``RELEASE_NAME`` should be set to the currently installed namespace and release:

.. code-block:: bash

    NAMESPACE=skypilot # TODO: change to your installed namespace
    RELEASE_NAME=skypilot # TODO: change to your installed release name
    # Set up basic username/password HTTP auth
    WEB_USERNAME=skypilot
    WEB_PASSWORD=yourpassword
    AUTH_STRING=$(htpasswd -nb $WEB_USERNAME $WEB_PASSWORD)

Use ``helm upgrade`` to redeploy the API server.

.. code-block:: console

    $ # --reuse-values is critical to keep the old values that aren't being updated here.
    $ helm upgrade -n $NAMESPACE $RELEASE_NAME skypilot/skypilot-nightly --devel --reuse-values \
            --set apiService.enableUserManagement=true \
            --set apiService.initialBasicAuthCredentials=$AUTH_STRING

Flags explanation:

* :ref:`--set apiService.enableUserManagement=true <helm-values-apiService-enableUserManagement>`: Enable basic auth and user management in the API server.
* :ref:`--set apiService.initialBasicAuthCredentials=$AUTH_STRING <helm-values-apiService-initialBasicAuthCredentials>`: Set the initial basic auth credentials in the API server, if this is not set, a default user ``skypilot`` with password ``skypilot`` will be created.

Now, you can use ``sky api login -e <ENDPOINT>``, for example, ``sky api login -e http://skypilot:yourpassword@myendpoint.com:30050`` to go though the login flow for the CLI. All the operations will be done with the user ``skypilot``.

.. note::
   To support RBAC, please create different usernames and passwords for different users. If you distribute the same username and password to multiple users, the users will share the same username and permissions.

User flow
~~~~~~~~~

The administrator can login to the SkyPilot dashboard with the initial username and password, and create new users and manage their roles through the SkyPilot dashboard.

After a user is created, the user can login to the API server with the username and password.
For example, the user ``user1`` with password ``password1`` is created, and the user can login to the API server with ``sky api login -e http://user1:password1@myendpoint.com:30050``.

.. figure:: ../../../images/client-server/basic-auth-user-flow.png
    :alt: SkyPilot with basic auth
    :align: center
    :width: 80%

Supported operations:

* The users with ``Admin`` role can create users, update password and role for all the users, and delete users.
* The users with ``Admin`` role can also import users from a CSV file and export users to a CSV file.

CSV file example:

.. code-block:: bash

    username,password,role
    user1,password1,admin
    user2,password2,user

* The users with ``User`` role can only view the users and their roles, and update their own password.
