.. _api-server-auth:

Authentication and RBAC
=========================

SkyPilot API server supports three authentication methods:

- **Basic auth**: Use an admin-configured username and password to authenticate.
- **Basic auth with RBAC**: Use an admin-configured username and password to as an ``Admin`` user, and manage other users and their roles.
- **SSO (recommended)**: Use an auth proxy (e.g.,
  `OAuth2 Proxy <https://oauth2-proxy.github.io/oauth2-proxy/>`__) to
  authenticate. For example, Okta, Google Workspace, or other SSO providers are supported.

Comparison of the three methods:

.. csv-table::
    :header: "", "Basic Auth", "Basic Auth with RBAC", "SSO (recommended)"
    :widths: 20, 40, 40, 40
    :align: left

    "User identity", "Client's ``whoami`` + hash of MAC address", "Users created by the ``Admin`` user", "User email (e.g., ``who@skypilot.co``), read from ``X-Auth-Request-Email``"
    "SkyPilot RBAC", "Not supported", "Supported", "Supported"
    "Setup", "Automatically enabled", "Can be enabled during deployment with Helm", "Bring your Okta, Google Workspace, or other SSO provider"


.. _api-server-basic-auth:

Basic auth
----------

Basic auth is automatically enabled if you use the :ref:`helm chart
<sky-api-server-deploy>` to deploy the API server. See the ``AUTH_STRING``
environment variable in the deployment instructions.

Example login command:

.. code-block:: console

    $ sky api login -e http://username:password@<SKYPILOT_API_SERVER_ENDPOINT>

.. _api-server-basic-auth-rbac:

Basic auth with RBAC
--------------------

Basic auth with RBAC can be enabled if you use the :ref:`helm chart
<deploy-api-server-basic-auth>` to deploy the API server. See the ``AUTH_STRING``
environment variable in the deployment instructions.

Example login command:

.. code-block:: console

    $ sky api login -e http://username:password@<SKYPILOT_API_SERVER_ENDPOINT>

.. _api-server-auth-proxy:

SSO (recommended)
------------------

You can deploy the SkyPilot API server behind an web authentication proxy, such as `OAuth2 Proxy <https://oauth2-proxy.github.io/oauth2-proxy/>`__, to use SSO providers such as :ref:`Okta or Google Workspace <oauth2-proxy-oidc>`.

The SkyPilot implementation is flexible and will work with most cookie-based browser auth proxies. See :ref:`auth-proxy-user-flow` and :ref:`auth-proxy-byo` for details. To set up Okta or Google Workspace, see :ref:`oauth2-proxy-oidc`.

.. image:: ../images/client-server/auth-proxy-user-flow.svg
    :alt: SkyPilot with auth proxy
    :align: center
    :width: 100%

.. _auth-proxy-user-flow:

User flow
~~~~~~~~~

While logging into an API server, SkyPilot will attempt to detect an auth proxy. If detected, the user must log in via a browser:

.. code-block:: console

    $ sky api login -e http://<SKYPILOT_API_SERVER_ENDPOINT>
    A web browser has been opened at http://<SKYPILOT_API_SERVER_ENDPOINT>/token?local_port=8000. Please continue the login in the web browser.

Login in the browser to authenticate as required by the auth proxy.

.. image:: ../images/client-server/login.png
    :alt: Okta and Google auth pages
    :align: center
    :width: 100%

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

.. _oauth2-proxy-okta:
.. _oauth2-proxy-oidc:

Setting up the proxy (Okta, Google Workspace, etc)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The SkyPilot API server helm chart can also deploy and configure `OAuth2 Proxy <https://oauth2-proxy.github.io/oauth2-proxy/>`__ to provide an out-of-the-box auth proxy setup.

The instructions below cover :ref:`Okta <okta-oidc-setup>` and :ref:`Google Workspace <google-oidc-setup>`, but any provider compatible with the OIDC spec should work.

Here's how to set it up:

* Set up your auth provider (pick one):

  * :ref:`Set up in Okta <okta-oidc-setup>`

  * :ref:`Set up Google Workspace login <google-oidc-setup>`

* :ref:`Deploy in Helm <oidc-oauth2-proxy-helm>`

.. _okta-oidc-setup:

Create application in Okta
^^^^^^^^^^^^^^^^^^^^^^^^^^

To use Okta, you will need to create a new application in the Okta admin panel.

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

You can now proceed to :ref:`the Helm deployment <oidc-oauth2-proxy-helm>`.

.. _google-oidc-setup:

Create Google Workspace client in GCP
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

To log in with Google Workspace, you will need to create a client in a GCP project.

Each GCP project can contain multiple "clients," but only a single application configuration. Depending on your use-case, you may want to create a new GCP project for authentication.

Once you have selected a GCP project, go to the `Clients page within Google Auth Platform <https://console.cloud.google.com/auth/clients>`__.

Configure Google Auth Platform
''''''''''''''''''''''''''''''

If you have not used Google Auth Platform in this GCP project, you will see a setup screen.

.. image:: ../images/client-server/google-auth-initial-setup.png
    :alt: Setup screen for Google Auth Platform
    :align: center
    :width: 70%

If you see the Clients page rather than this setup screen, you can proceed to the :ref:`Client setup <google-oidc-client-setup>`. Otherwise, click "Get started" to set up the GCP project with Google Auth Platform.

1. App Information

   This configures the display name on the auth prompt for all clients in the GCP project, as well as an email that users can see while logging in. Choose values that make sense for your team.

2. Audience

   **Recommended: "Internal"**

   Choosing "External" may allow users outside your organization to log in, and may require additional verification steps from Google. If you choose "External", you may want to use :ref:`the auth proxy email domain filter <helm-values-ingress-oauth2-proxy-email-domain>` to prevent users from outside your organization from logging in to SkyPilot.

3. Contact Information

   Provide a good point of contact for your organization.

4. Finish

   Accept the necessary terms and create the configuration.

.. _google-oidc-client-setup:

Create GCP auth client
''''''''''''''''''''''

Click "Create OAuth client" or visit `the Clients page <https://console.cloud.google.com/auth/clients>`__ and click "Create".

Select the necessary config values:

* **Application type:** Choose "Web application".
* **Name:** Choose a name that will be meaningful to you, such as "SkyPilot auth proxy". This name is internal-only.
* **Authorized redirect URIs**: Click "Add URI", and add ``<ENDPOINT>/oauth2/callback``, where ``<ENDPOINT>`` is your API server endpoint. e.g. ``http://skypilot.example.com/oauth2/callback``

.. image:: ../images/client-server/google-auth-setup.png
    :alt: Create an OIDC client in Google Auth Platform
    :align: center
    :width: 100%

Click "Create".

Copy down the **Client ID** and **Client secret**. After exiting this screen, you won't be able to access the client secret without creating a new client. You will need them for :ref:`deploying to Helm <oidc-oauth2-proxy-helm>`.

.. note::

    If Google Auth Platform audience is set to **"External"** in your GCP project, anyone with a Google account may be able to log in.

    You can set an :ref:`email domain filter <helm-values-ingress-oauth2-proxy-email-domain>` in the Helm chart, which is the ``<EMAIL DOMAIN>`` value in the :ref:`Helm deployment instructions below <oidc-oauth2-proxy-helm>`.

    To check if your audience is set to "Internal" or "External", go to the `Audience page <https://console.cloud.google.com/auth/audience>`__ under Google Auth Platform. Under "User type", you should see "Internal" or "External". You can switch between Internal and External audience, but it will affect all auth clients in the GCP project.

.. _oidc-oauth2-proxy-helm:

Deploy in Helm
^^^^^^^^^^^^^^^

Set up the environment variables for your API server deployment. ``NAMESPACE`` and ``RELEASE_NAME`` should be set to the currently installed namespace and release:

.. code-block:: bash

    NAMESPACE=skypilot # TODO: change to your installed namespace
    RELEASE_NAME=skypilot # TODO: change to your installed release name

Use ``helm upgrade`` to redeploy the API server helm chart with the ``skypilot-oauth2-proxy`` deployment. Replace the config values:

* ``<CLIENT ID>``: Copy from the auth provider dashboard

* ``<CLIENT SECRET>``: Copy from the auth provider dashboard

* ``<ISSUER URL>``

  * **Okta**: Your Okta login URL, like ``https://acme-corp.okta.com``

  * **Google Workspace**: Set to ``https://accounts.google.com``

* ``<EMAIL DOMAIN>``: Optionally :ref:`restrict login to specific email domains <helm-values-ingress-oauth2-proxy-email-domain>`


.. code-block:: console

    $ # --reuse-values is critical to keep the old values that aren't being updated here.
    $ helm upgrade -n $NAMESPACE $RELEASE_NAME skypilot/skypilot-nightly --devel --reuse-values \
      --set ingress.oauth2-proxy.enabled=true \
      --set ingress.oauth2-proxy.oidc-issuer-url=https://<ISSUER URL> \
      --set ingress.oauth2-proxy.client-id=<CLIENT ID> \
      --set ingress.oauth2-proxy.client-secret=<CLIENT SECRET> \
      --set ingress.oauth2-proxy.email-domain=<EMAIL DOMAIN> # optional

.. _auth-proxy-client-secret:

For better security, you can also store the client details in a Kubernetes secret instead of passing them as Helm values:

.. code-block:: console

    $ # Create a secret with your OIDC credentials
    $ kubectl create secret generic oauth2-proxy-credentials -n $NAMESPACE \
      --from-literal=client-id=<CLIENT ID> \
      --from-literal=client-secret=<CLIENT SECRET>

    $ # Deploy using the secret
    $ helm upgrade -n $NAMESPACE $RELEASE_NAME skypilot/skypilot-nightly --devel --reuse-values \
      --set ingress.oauth2-proxy.enabled=true \
      --set ingress.oauth2-proxy.oidc-issuer-url=https://<ISSUER URL> \
      --set ingress.oauth2-proxy.client-details-from-secret=oauth2-proxy-credentials \
      --set ingress.oauth2-proxy.email-domain=<EMAIL DOMAIN> # optional


.. note::
   Both ``client-id``/``client-secret`` (dash format) and ``client_id``/``client_secret`` (underscore format) key names in secrets are supported. The system will automatically detect which format is present in your secret. This provides compatibility with different secret management systems - for example, HashiCorp Vault requires underscores in key names.

To make sure it's working, visit your endpoint URL in a browser. You should be redirected to your auth provider to sign in.

Now, you can use ``sky api login -e <ENDPOINT>`` to go though the login flow for the CLI.

Auth integration FAQ
^^^^^^^^^^^^^^^^^^^^^

* [Okta] I'm getting a `400 Bad Request error <https://support.okta.com/help/s/article/The-redirect-uri-parameter-must-be-an-absolute-URI?language=en_US>`__  from Okta when I open the endpoint URL in a browser.

  Your proxy may be configured to redirect to a different URL (e.g., changing the URL from ``http`` to ``https``). Make sure to set the ``Sign-in redirect URIs`` in Okta application settings to all possible URLs that your proxy may redirect to, including HTTP and HTTPS endpoints.


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

Service account architecture
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. image:: ../images/client-server/service-account-architecture.svg
    :alt: Service Account Architecture with Auth Proxy
    :align: center
    :width: 90%

Service accounts are enabled by default in the SkyPilot API server helm chart. To disable them, set ``--set apiService.enableServiceAccounts=false`` in the helm upgrade command.

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

If the ``X-Auth-Request-Email`` header is set by your auth proxy, SkyPilot will use it as the username in all requests. You can customize the authentication header name if your auth proxy uses a different header than the default ``X-Auth-Request-Email``.

.. code-block:: bash

    # Using Helm chart values
    helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
      --namespace $NAMESPACE \
      --reuse-values \
      --set apiService.authUserHeaderName=X-Custom-User-Header

.. code-block:: bash

    # Using environment variable - not necessary if using Helm
    export SKYPILOT_AUTH_USER_HEADER=X-Custom-User-Header
    sky api start --deploy


SkyPilot RBAC
-------------

SkyPilot provides basic RBAC (role-based access control) support. Two roles are supported:

- **User**: Use SkyPilot as usual to launch and manage resources (clusters, jobs, etc.).
- **Admin**: Manage SkyPilot API server settings, users, and workspaces.

RBAC support is enabled when :ref:`SSO authentication <api-server-auth-proxy>` or :ref:`basic auth with RBAC <api-server-basic-auth-rbac>` is used (not when using :ref:`basic auth <api-server-basic-auth>`).

Config :ref:`config-yaml-rbac-default-role` determines whether a new
SkyPilot user is created with the ``user`` or ``admin`` role. By default, it is
set to ``admin`` to ease first-time setup.

User management
~~~~~~~~~~~~~~~

When SSO authentication is used, SkyPilot automatically creates a user for each authenticated user. The user's email is used as the username.

When basic auth with RBAC is used, the initial admin user is created with the ``admin`` role and it can create new users and manage their roles in the dashboard.

Admins can click on the **Users** tab in the SkyPilot dashboard to manage users and their roles.

.. figure:: ../images/client-server/users.png
    :align: center
    :width: 80%

Supported operations:

* ``Admin`` role can create users (only when basic auth with RBAC is used), update the role for all users, and delete users.
* ``User`` role can view all users and their roles.
