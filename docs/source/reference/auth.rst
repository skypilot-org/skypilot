.. _api-server-auth:

Authentication and RBAC
=========================

SkyPilot API server supports two authentication methods:

- **Basic auth**: Use an admin-configured username and password to authenticate.
- **SSO (recommended)**: Use an auth proxy (e.g.,
  `OAuth2 Proxy <https://oauth2-proxy.github.io/oauth2-proxy/>`__) to
  authenticate. For example, Okta, Google Workspace, or other SSO providers are supported.

Comparison of the two methods:

.. csv-table::
    :header: "", "Basic Auth", "SSO (recommended)"
    :widths: 20, 40, 40
    :align: left

    "User identity", "Client's ``whoami`` + hash of MAC address", "User email (e.g., ``who@skypilot.co``), read from ``X-Auth-Request-Email``"
    "SkyPilot RBAC", "Not supported", "Supported"
    "Setup", "Automatically enabled", "Bring your Okta, Google Workspace, or other SSO provider"


.. _api-server-basic-auth:

Basic auth
----------

Basic auth is automatically enabled if you use the :ref:`helm chart
<sky-api-server-deploy>` to deploy the API server. See the ``AUTH_STRING``
environment variable in the deployment instructions.

Example login command:

.. code-block:: console

    $ sky api login -e http://username:password@<SKYPILOT_API_SERVER_ENDPOINT>

.. _api-server-auth-proxy:

SSO (recommended)
------------------

You can deploy the SkyPilot API server behind an web authentication proxy, such as `OAuth2 Proxy <https://oauth2-proxy.github.io/oauth2-proxy/>`__, to use SSO providers such as :ref:`Okta <oauth2-proxy-okta>` or Google Workspace.

The SkyPilot implementation is flexible and will work with most cookie-based browser auth proxies. See :ref:`auth-proxy-user-flow` and :ref:`auth-proxy-byo` for details. To set up Okta, see :ref:`oauth2-proxy-okta`.

.. image:: ../images/client-server/auth-proxy-user-flow.svg
    :alt: SkyPilot with auth proxy
    :align: center
    :width: 80%

.. _auth-proxy-user-flow:

User flow
~~~~~~~~~

While logging into an API server, SkyPilot will attempt to detect an auth proxy. If detected, the user must log in via a browser:

.. code-block:: console

    $ sky api login -e http://<SKYPILOT_API_SERVER_ENDPOINT>
    A web browser has been opened at http://<SKYPILOT_API_SERVER_ENDPOINT>/token?local_port=8000. Please continue the login in the web browser.

Login in the browser to authenticate as required by the auth proxy.

.. image:: ../images/client-server/okta.png
    :alt: Okta auth page
    :align: center
    :width: 60%

After authentication, the CLI will automatically copy the relevant auth cookies from the browser into the CLI.

.. code-block:: console

    ...
    Logged into SkyPilot API server at: http://<SKYPILOT_API_SERVER_ENDPOINT>
    └── Dashboard: http://<SKYPILOT_API_SERVER_ENDPOINT>/dashboard

SkyPilot will automatically use the user email from the auth proxy to create a user in the SkyPilot API server.

.. image:: ../images/client-server/cluster-users.png
    :alt: User emails in the SkyPilot dashboard
    :align: center
    :width: 70%

.. _service-accounts:

Optional: Service accounts
~~~~~~~~~~~~~~~~~~~~~~~~~~

You can also use service accounts to access SkyPilot API server programmatically without browser authentication, which is good for CI/CD pipelines, Airflow integration, etc.


Creating service accounts
^^^^^^^^^^^^^^^^^^^^^^^^^^

1. Navigate to **Users > Service Accounts** in the SkyPilot dashboard
2. Click **Create Service Account** and provide:
   
   * **Token Name**: Descriptive name (e.g., "pipeline")
   * **Expiration**: Optional (defaults to 30 days)

3. **Save the token immediately** - it won't be shown again
4. Assign appropriate role (admin/user)

.. image:: ../images/client-server/service-account.png
    :alt: Service account
    :align: center
    :width: 90%

Accessing the API server
^^^^^^^^^^^^^^^^^^^^^^^^

Authenticate with the service account token:

.. code-block:: console

    $ sky api login -e <ENDPOINT> --token <SERVICE_ACCOUNT_TOKEN>

Or, use the ``SKYPILOT_SERVICE_ACCOUNT_TOKEN`` environment variable:

.. code-block:: console

    $ export SKYPILOT_SERVICE_ACCOUNT_TOKEN=<SERVICE_ACCOUNT_TOKEN>
    $ sky api info

Example: GitHub actions (CI/CD)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: yaml

    # .github/workflows/deploy.yml
    - name: Configure SkyPilot
      run: sky api login -e ${{ vars.SKYPILOT_API_ENDPOINT }} --token ${{ secrets.SKYPILOT_SERVICE_ACCOUNT_TOKEN }}
        
    - name: Launch training job
      run: sky launch training.yaml


.. _oauth2-proxy-okta:

Setting up Okta
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The SkyPilot API server helm chart can also deploy and configure `OAuth2 Proxy <https://oauth2-proxy.github.io/oauth2-proxy/>`__ to provide an out-of-the-box auth proxy setup.

To integrate with Okta, OAuth2 Proxy uses OpenID Connect (OIDC) and follows the `Authorization Code flow <https://developer.okta.com/docs/guides/implement-grant-type/authcode/main/>`__ recommended by Okta.

Here's how to set it up:

Create application in Okta
^^^^^^^^^^^^^^^^^^^^^^^^^^^

1. From your Okta admin panel, navigate to **Applications > Applications**, then click the **Create App Integration** button.

   * **Sign-in method:** ``OIDC - OpenID Connect``
   * **Application type:** ``Web Application``

.. image:: ../images/client-server/okta-setup.png
    :alt: SkyPilot token page
    :align: center
    :width: 80%

2. Configure the application:

   * **App integration name:** ``SkyPilot API Server`` or any other name.
   * **Sign-in redirect URIs:** ``<ENDPOINT>/oauth2/callback``, where ``<ENDPOINT>`` is your API server endpoint. e.g. ``http://skypilot.example.com/oauth2/callback``
   * **Assignments > Controlled access:** ``Allow everyone in your organization to access``, unless you want to limit access to select groups.

3. Click **Save**. You will need the Client ID and a Client Secret in the next step.

Deploy in Helm
^^^^^^^^^^^^^^^

Set up the environment variables for your API server deployment. ``NAMESPACE`` and ``RELEASE_NAME`` should be set to the currently installed namespace and release:

.. code-block:: bash

    NAMESPACE=skypilot # TODO: change to your installed namespace
    RELEASE_NAME=skypilot # TODO: change to your installed release name

Use ``helm upgrade`` to redeploy the API server helm chart with the ``skypilot-oauth2-proxy`` deployment. Replace ``<CLIENT ID>`` and ``<CLIENT SECRET>`` with the values from the Okta admin console above, and ``<OKTA URL>`` with your Okta login URL.

.. code-block:: console

    $ # --reuse-values is critical to keep the old values that aren't being updated here.
    $ helm upgrade -n $NAMESPACE $RELEASE_NAME skypilot/skypilot-nightly --devel --reuse-values \
      --set ingress.oauth2-proxy.enabled=true \
      --set ingress.oauth2-proxy.oidc-issuer-url=https://<OKTA URL>.okta.com \
      --set ingress.oauth2-proxy.client-id=<CLIENT ID> \
      --set ingress.oauth2-proxy.client-secret=<CLIENT SECRET>

.. _auth-proxy-client-secret:

For better security, you can also store the client details in a Kubernetes secret instead of passing them as Helm values:

.. code-block:: console

    $ # Create a secret with your Okta credentials
    $ kubectl create secret generic oauth2-proxy-credentials -n $NAMESPACE \
      --from-literal=client-id=<CLIENT ID> \
      --from-literal=client-secret=<CLIENT SECRET>

    $ # Deploy using the secret
    $ helm upgrade -n $NAMESPACE $RELEASE_NAME skypilot/skypilot-nightly --devel --reuse-values \
      --set ingress.oauth2-proxy.enabled=true \
      --set ingress.oauth2-proxy.oidc-issuer-url=https://<OKTA URL>.okta.com \
      --set ingress.oauth2-proxy.client-details-from-secret=oauth2-proxy-credentials

To make sure it's working, visit your endpoint URL in a browser. You should be redirected to Okta to sign in.

Now, you can use ``sky api login -e <ENDPOINT>`` to go though the login flow for the CLI.

Okta integration FAQ
^^^^^^^^^^^^^^^^^^^^^

* I'm getting a `400 Bad Request error <https://support.okta.com/help/s/article/The-redirect-uri-parameter-must-be-an-absolute-URI?language=en_US>`__  from Okta when I open the endpoint URL in a browser.

  Your proxy may be configured to redirect to a different URL (e.g., changing the URL from ``http`` to ``https``). Make sure to set the ``Sign-in redirect URIs`` in Okta application settings to all possible URLs that your proxy may redirect to, including HTTP and HTTPS endpoints.


Service account architecture
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. image:: ../images/client-server/service-account-architecture.svg
    :alt: Service Account Architecture with Auth Proxy
    :align: center
    :width: 90%

Service accounts is enabled by default in the SkyPilot API server helm chart. To disable it, set ``--set apiService.enableServiceAccounts=false`` in the helm upgrade command.

.. _auth-proxy-byo:

Optional: Bring your own auth proxy
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Under the hood, SkyPilot uses cookies just like a browser to authenticate to an auth proxy. This means that most web authentication proxies should work with the SkyPilot API server. This can be convenient if you already have a standardized auth proxy setup for services you deploy.

To bring your own auth proxy, just configure it in front of the underlying SkyPilot API server, just like any other web application. Then, use the proxy's address as the API server endpoint.

To log into the CLI, use ``sky api login`` as normal - it should automatically detect the auth proxy and redirect you into the special login flow.

During the login flow, the token provided by the web login will encode the cookies used for authentication. By pasting this into the CLI, the CLI will also be able to authenticate using the cookies.

.. image:: ../images/client-server/auth-proxy-internals.svg
    :alt: SkyPilot auth proxy architecture
    :align: center
    :width: 100%

.. note::

    If your auth proxy is not automatically detected, try using ``sky api login --cookies`` to force auth proxy mode.

If the ``X-Auth-Request-Email`` header is set by your auth proxy, SkyPilot will use it as the username in all requests.

SkyPilot RBAC
-------------

SkyPilot provides basic RBAC (role-based access control) support. Two roles are supported:

- **User**: Use SkyPilot as usual to launch and manage resources (clusters, jobs, etc.).
- **Admin**: Manage SkyPilot API server settings, users, and workspaces.

RBAC support is enabled only when :ref:`SSO authentication <api-server-auth-proxy>` is used (not when using :ref:`basic auth <api-server-basic-auth>`).

Config :ref:`config-yaml-rbac-default-role` determines whether a new
SkyPilot user is created with the ``user`` or ``admin`` role. By default, it is
set to ``admin`` to ease first-time setup.

User management
~~~~~~~~~~~~~~~

SkyPilot automatically creates a user for each authenticated user. The user's email is used as the username.

Admins can click on the **Users** tab in the SkyPilot dashboard to manage users and their roles.

.. figure:: ../images/client-server/users.png
    :align: center
    :width: 80%

Supported operations:

* ``Admin`` role can create users, update the role for all users, and delete users.
* ``User`` role can view all users and their roles.




