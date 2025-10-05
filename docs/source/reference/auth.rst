.. _api-server-auth:

Authentication and RBAC
=========================

SkyPilot API server supports two authentication methods:

.. We will eventually support N+1 kinds of authentications:
.. 1. Service account token based authentication, which will enabled by default in helm deployment to ensure the deployed server is protected;
.. 2. SSO;
.. N: (another authentication method, authentication schemes 1~N are handled by the API server and can be used at the same time, a.k.a. unified authentication)
.. N+1: Proxy authentication, where the reverse proxy in front of the API server handles the authentication and pass the identity header to the API server. This is mutually exclusive with authentication schemes 1~N. For clarity maybe this part will be hosted in another doc.
.. TODO(aylei): replace basic auth with proxy auth for clarity after we support service account token based authentication to be used along.

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

.. _api-server-oauth:

SSO (recommended)
------------------

You can configure the SkyPilot API server to use an SSO providers such as :ref:`Okta, Google Workspace, or Microsoft Entra ID <oauth-oidc>` for authentication.

The SkyPilot implementation is flexible and will also work with most cookie-based browser auth proxies. See :ref:`oauth-user-flow` and :ref:`auth-proxy-byo` for details. To set up Okta, Google Workspace, or Microsoft Entra ID, see :ref:`oauth-oidc`.

.. image:: ../images/client-server/oauth-user-flow.svg
    :alt: SkyPilot with OAuth
    :align: center
    :width: 100%

.. _oauth-user-flow:

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

.. _oauth-okta:
.. _oauth-google:
.. _oauth-microsoft:
.. _oauth-oidc:

Setting up OAuth (Okta, Google Workspace, Microsoft Entra ID, etc)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The SkyPilot API server helm chart provides out-of-the-box support for setting up OAuth on API server. An `OAuth2 Proxy <https://oauth2-proxy.github.io/oauth2-proxy/>`__ will be deployed under the hood and the API server will be configured to use it for authentication.

The instructions below cover :ref:`Okta <okta-oidc-setup>`, :ref:`Google Workspace <google-oidc-setup>`, and :ref:`Microsoft Entra ID <microsoft-oidc-setup>`, but any provider compatible with the OIDC spec should work.

Here's how to set it up:

* Set up your auth provider (pick one):

  * :ref:`Set up in Okta <okta-oidc-setup>`

  * :ref:`Set up Google Workspace login <google-oidc-setup>`

  * :ref:`Set up Microsoft Entra ID <microsoft-oidc-setup>`

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

.. _microsoft-oidc-setup:

Create application in Microsoft Entra ID
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

To use Microsoft Entra ID, you will need to add an "App registration" in the Microsoft Entra ID portal.

.. image:: ../images/client-server/microsoft-auth-initial.png
    :alt: Microsoft Entra ID setup
    :align: center
    :width: 80%

1. Configure the application:

   * **Name:** Choose a name that will be meaningful to you, such as "SkyPilot auth proxy". This name is internal-only.
   * **Supported account types:** ``Accounts in this organizational directory only`` (recommended)
   * **Redirect URI:**

     * **Select a platform:** ``Web``
     * **URI:** ``<ENDPOINT>/oauth2/callback``, where ``<ENDPOINT>`` is your API server endpoint. e.g. ``https://skypilot.example.com/oauth2/callback``

     .. note::
        Microsoft Entra ID requires HTTPS, so make sure to use ``https://`` rather than ``http://`` in your redirect URI.

2. Click **Register**. You will need the **Client ID** (``Application (client) ID``) and **Tenant ID** (``Directory (tenant) ID``) from the overview page.

3. In the **App registrations** detail page for the newly created application, click **Add a certificate or secret** and create a new client secret.
   You will need the **Client Secret** in the next step.

**Microsoft Entra ID example values.yaml:**

.. code-block:: yaml

    auth:
      oauth:
        enabled: true
        oidc-issuer-url: https://login.microsoftonline.com/<TENANT_ID>/v2.0
        client-id: <CLIENT_ID>
        client-secret: <CLIENT_SECRET>
        use-https: true
        email-domain: <YOUR_DOMAIN>  # optional

You can now proceed to :ref:`the Helm deployment <oidc-oauth2-proxy-helm>`.

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

  * **Microsoft Entra ID**: Set to ``https://login.microsoftonline.com/<TENANT_ID>/v2.0``, where ``<TENANT_ID>`` is your Microsoft Entra ID tenant ID

* ``<EMAIL DOMAIN>``: Optionally :ref:`restrict login to specific email domains <helm-values-ingress-oauth2-proxy-email-domain>`


.. code-block:: console

    $ # --reuse-values is critical to keep the old values that aren't being updated here.
    $ helm upgrade -n $NAMESPACE $RELEASE_NAME skypilot/skypilot-nightly --devel --reuse-values \
      --set auth.oauth.enabled=true \
      --set auth.oauth.oidc-issuer-url=https://<ISSUER URL> \
      --set auth.oauth.client-id=<CLIENT ID> \
      --set auth.oauth.client-secret=<CLIENT SECRET> \
      --set auth.oauth.email-domain=<EMAIL DOMAIN> # optional

If you are using Microsoft Entra ID or any other provider that requires the redirect URI to be HTTPS, you will need to set one additional Helm value:

.. code-block:: console

    $ helm upgrade -n $NAMESPACE $RELEASE_NAME skypilot/skypilot-nightly --devel --reuse-values \
      --set auth.oauth.use-https=true

.. _oauth-client-secret:

For better security, you can also store the client details in a Kubernetes secret instead of passing them as Helm values:

.. code-block:: console

    $ # Create a secret with your OIDC credentials
    $ kubectl create secret generic oauth2-proxy-credentials -n $NAMESPACE \
      --from-literal=client-id=<CLIENT ID> \
      --from-literal=client-secret=<CLIENT SECRET>

    $ # Deploy using the secret
    $ helm upgrade -n $NAMESPACE $RELEASE_NAME skypilot/skypilot-nightly --devel --reuse-values \
      --set auth.oauth.enabled=true \
      --set auth.oauth.oidc-issuer-url=https://<ISSUER URL> \
      --set auth.oauth.client-details-from-secret=oauth2-proxy-credentials \
      --set auth.oauth.email-domain=<EMAIL DOMAIN> # optional


.. note::
   Both ``client-id``/``client-secret`` (dash format) and ``client_id``/``client_secret`` (underscore format) key names in secrets are supported. The system will automatically detect which format is present in your secret. This provides compatibility with different secret management systems - for example, HashiCorp Vault requires underscores in key names.

To make sure it's working, visit your endpoint URL in a browser. You should be redirected to your auth provider to sign in.

Now, you can use ``sky api login -e <ENDPOINT>`` to go though the login flow for the CLI.

.. _oauth-migration-guide:

OAuth migration guide
^^^^^^^^^^^^^^^^^^^^^

.. dropdown:: Migration guide for auth proxy based authentication (before SkyPilot v0.10.2)

    .. TODO(aylei): Add the nightly version after this change get released

    Starting with SkyPilot v0.10.2, the API server supports built-in OAuth2 integration (delegate authentication to `OAuth2 Proxy <https://github.com/oauth2-proxy/oauth2-proxy>`_ under the hood) without ingress support. This is more flexible and can work seamlessly with other authentication schemes supported by the API server.

    If you are using the auth proxy in ingress (enabled by setting ``ingress.oauth2-proxy.enabled=true`` in the Helm chart), you can migrate to the new OAuth2 integration by setting ``auth.oauth.enabled=true`` and migrate other settings from ``ingress.oauth2-proxy.*`` to ``auth.oauth.*`` in the Helm chart:

    .. note::

        Both the API server docker image and the helm chart should be updated to version 0.10.2 or later to use the new OAuth2 integration.

    .. code-block:: console

        # NAMESPACE and RELEASE_NAME are the same as the ones used in the Helm deployment
        $ helm get values $RELEASE_NAME -n $NAMESPACE -o yaml > values.yaml

        # Edit values.yaml, move the values from ingress.oauth2-proxy.* to auth.oauth.*
        # Preview the changes, you should see the following diff:
        $ diff values.yaml <(sed 's/^ingress:/auth:/;s/^  oauth2-proxy:/  oauth:/' values.yaml)
        4,5c4,5
        < ingress:
        <   oauth2-proxy:
        ---
        > auth:
        >   oauth:
        $ sed -i 's/^ingress:/auth:/;s/^  oauth2-proxy:/  oauth:/' values.yaml

        # Upgrade the helm chart with mutated values
        $ helm upgrade -n $NAMESPACE $RELEASE_NAME skypilot/skypilot-nightly --devel --reset-then-reuse-values \
          -f values.yaml

    The migration will not break authenticated clients as long as the OAuth provider config is not changed.


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

    If your auth proxy is not automatically detected or you would like to login with a different identity, try using ``sky api login --relogin`` to force relogin.

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

.. _cloudflare-zero-trust:

Optional: Cloudflare Zero Trust and WARP
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

You can configure Cloudflare Zero Trust with WARP to provide seamless authentication for your SkyPilot API server. This setup allows CLI/API requests to bypass login when the device is connected to Warp, while browser users are still challenged with SSO.

Prerequisites
^^^^^^^^^^^^^

* A domain managed by Cloudflare (e.g. ``skypilot.org``)
* Cloudflare Zero Trust subscription
* DNS record pointing your API ingress LoadBalancer IP from the SSO setup in the same Cloudflare account as your Zero Trust setup (this example uses ``zerotrust.skypilot.org``)
* SkyPilot API server configured with SSO (you will need your client ID and secret)


Step 1: Create policies
^^^^^^^^^^^^^^^^^^^^^^^

* Navigate to **Zero Trust → Access → Policies → Add a policy**

  * **Policy 1: allow-<yourorg>**

    * **Action**: ``Allow``
    * **Include**: 
       
      * **Selector**: ``Emails ending in``
      * **Value**: ``@<yourorg>.com``

  * **Policy 2: bypass-warp**

    * **Action**: ``Bypass``
    * **Include**: 
       
      * **Selector**: ``Warp``
      * **Value**: ``Warp``

Step 2: Configure your SSO provider as an identity provider
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

1. Navigate to **Zero Trust → Settings → Authentication → Login methods → Add new**

2. Configure the identity provider in Cloudflare:

   * **App ID**: ``<SSO_CLIENT_ID>``
   * **Client secret**: ``<SSO_CLIENT_SECRET>``

3. Add Cloudflare Authorized JavaScript origins and redirect URIs to your SSO client:

   * **Authorized JavaScript origins**: ``https://<your-team-name>.cloudflareaccess.com``
   * **Redirect URIs**: ``https://<your-team-name>.cloudflareaccess.com/cdn-cgi/access/callback``

.. note::
    
    You can find your team name under **Zero Trust → Settings → Custom Pages → Team Domain**.
    It will be listed as ``<your-team-name>.cloudflareaccess.com``.

Step 3: Create a Cloudflare access application
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

1. Navigate to **Zero Trust → Access → Applications → Add an application**

2. Configure the application:

   * **Application type**: ``Self-hosted``
   * **Application name**: ``SkyPilot API``
   * **Session duration**: ``24 hours``
   * **Public hostname**:
   
     * **Subdomain**: ``DNS_RECORD_NAME`` (e.g. ``zerotrust``)
     * **Domain**: ``DNS_RECORD_DOMAIN`` (e.g. ``skypilot.org``)
   * **Access policies → Select existing policies**:

     * **Allow policy**: ``allow-<yourorg>``
     * **Bypass policy**: ``bypass-warp``

3. Save the application.

This configuration allows:

* CLI/API requests to bypass login if the device is on WARP
* Browser users to be challenged with SSO


Step 4: Enable device enrollment for WARP
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

1. Navigate to **Zero Trust → Settings → WARP client → Device enrollment permissions → Manage**

2. Create an enrollment rule:

   * **Select existing policies**: ``allow-<yourorg>``

3. Save

This restricts WARP enrollment to only your team members.

Step 5: Deploy WARP client to your team
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

You you deploy the WARP client to your team in one of two ways:

1. Manual deployment (`Cloudflare WARP client manual deployment <https://developers.cloudflare.com/cloudflare-one/connections/connect-devices/warp/deployment/manual-deployment/>`__)
2. Automatic deployment (`Cloudflare WARP client MDM deployment <https://developers.cloudflare.com/cloudflare-one/connections/connect-devices/warp/deployment/mdm-deployment/>`__)

Each user performs this setup once per device.


Step 6: Test access
^^^^^^^^^^^^^^^^^^^

Test that the configuration is working:

.. code-block:: console

    # Set your DNS record variables
    $ DNS_RECORD_NAME=<your_dns_record_name>  # e.g. zerotrust
    $ DNS_RECORD_DOMAIN=<your_dns_record_domain>  # e.g. skypilot.org

    # Test the API health endpoint
    $ curl -i https://${DNS_RECORD_NAME}.${DNS_RECORD_DOMAIN}/api/health
    # Should return 200 OK

    # Test SkyPilot API login
    $ sky api login -e https://${DNS_RECORD_NAME}.${DNS_RECORD_DOMAIN}
    # Should complete login without browser redirect


SkyPilot RBAC
-------------

SkyPilot provides basic RBAC (role-based access control) support. Two roles are supported:

- **User**: Use SkyPilot as usual to launch and manage resources (clusters, jobs, etc.).
- **Admin**: Manage SkyPilot API server settings, users, and workspaces.

RBAC support is enabled only when :ref:`SSO authentication <api-server-oauth>` is used (not when using :ref:`basic auth <api-server-basic-auth>`).

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

* ``Admin`` role can update the role for all users, and delete users.
* ``User`` role can view all users and their roles.
