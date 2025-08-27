Deploying a SkyPilot API Server on GKE backed by GCP Cloud SQL
==============================================================

In this example, a SkyPilot API server is deployed on a GKE cluster with a persistent database backed by GCP Cloud SQL.

SkyPilot API server deployed on k8s clusters can use either **password authentication** or **IAM authentication** to access the Cloud SQL instance.

**Password authentication** is the less secure option between the two, but it works with any k8s cluster (not necessarily GKE).

**IAM authentication** is a more secure authentication method that allows the GKE cluster to access the database using a GCP service account.
However, it requires a GKE cluster with Workload Identity enabled.

.. note::

    IAM authentication is recommended for production deployments on GKE clusters.

Prerequisites
-------------

* `Helm <https://helm.sh/docs/intro/install/>`_
* `kubectl <https://kubernetes.io/docs/tasks/tools/#kubectl>`_
* `gcloud CLI <https://cloud.google.com/sdk/docs/install>`_
* Access to GCP console

Create a GKE cluster
--------------------

.. tab-set::

    .. tab-item:: IAM Auth
      :sync: iam-auth

        Create a GKE cluster with Workload Identity enabled.

        **CLI:** 
        
        Add ``--enable-workload-identity`` flag to ``gcloud container clusters create`` command as shown:

        .. code-block:: bash

            gcloud container clusters create <cluster-name> \
                ...
                --enable-workload-identity

        **Web Console:**

        When creating a standard GKE cluster, go to the ``Security`` tab and check ``Enable Workload Identity``.

        .. note::
        
            Retroactively enabling Workload Identity on an existing cluster is complicated and is not recommended.

    .. tab-item:: Password Auth
      :sync: password-auth

        Create a GKE cluster as you normally would.

Create a GCP service account to use with the API server
-------------------------------------------------------

In this step, a GCP service account is created to use with the API server.

.. tab-set::

    .. tab-item:: IAM Auth
      :sync: iam-auth

        - Go to the `Service Accounts <https://console.cloud.google.com/iam-admin/serviceaccounts>`_ page of the ``IAM and Admin`` console
        - Click on "Create Service Account"
        - Set the service account name and ID to ``skypilot-cloud-sql-access``
        - Click on "Create and Continue" to move to the Permissions page.
        - Add ``Cloud SQL Client`` and ``Cloud SQL Instance User`` roles to the service account.
        - Click on "Continue", then "Done" to create the service account.

    .. tab-item:: Password Auth
      :sync: password-auth

        - Go to the `Service Accounts <https://console.cloud.google.com/iam-admin/serviceaccounts>`_ page of the ``IAM and Admin`` console
        - Click on "Create Service Account"
        - Set the service account name and ID to ``skypilot-cloud-sql-access``
        - Click on "Create and Continue" to move to the Permissions page.
        - Add ``Cloud SQL Client`` role to the service account.
        - Click on "Continue", then "Done" to create the service account.
  
Create a cloud SQL instance
---------------------------

- Go to the `Cloud SQL console <https://console.cloud.google.com/sql/instances>`_
- Click on "Create instance"
- Select "PostgreSQL" as the database engine
- Set the instance ID to ``cloud-sql-skypilot-instance``
- Set the password for the ``postgres`` user.
- Select the region (and zone if applicable) where you want to create the instance. The region / zone of the database should match that of the GKE cluster.
- Click on "Create Instance"

Configure the cloud SQL instance
--------------------------------

Once the instance is created, we need to configure the instance to create a user and a database for SkyPilot API server.

To create a database, use `gcloud CLI <https://cloud.google.com/sdk/docs/install>`_ to run the following command:

.. code-block:: bash

    DB_NAME=skypilot-db
    DB_INSTANCE_NAME=cloud-sql-skypilot-instance
    gcloud sql databases create ${DB_NAME} --instance ${DB_INSTANCE_NAME}

To create a user, use `gcloud CLI <https://cloud.google.com/sdk/docs/install>`_ to run the following command:

.. tab-set::

    .. tab-item:: IAM Auth
      :sync: iam-auth

        .. code-block:: bash

            GCP_PROJECT_ID=<your gcp project id>
            GCP_SERVICE_ACCOUNT=skypilot-cloud-sql-access
            DB_INSTANCE_NAME=cloud-sql-skypilot-instance
            gcloud sql users create ${GCP_SERVICE_ACCOUNT}@${GCP_PROJECT_ID}.iam \
                --instance=${DB_INSTANCE_NAME} \
                --type=cloud_iam_service_account

        Since the service account user is not granted any privileges in the database by default,
        we need to grant the user the necessary privileges.

        - Go to the `Cloud SQL console <https://console.cloud.google.com/sql/instances>`_
        - Click on ``cloud-sql-skypilot-instance``
        - Click on ``Cloud SQL Studio`` tab on the side bar.
        - Authenticate to ``skypilot-db`` database using the ``postgres`` user.
        - Run the following SQL command to grant the user the necessary privileges:
        
        .. code-block:: sql
        
            GRANT "cloudsqlsuperuser" TO "skypilot-cloud-sql-access@<gcp-project-id>.iam"

    .. tab-item:: Password Auth
      :sync: password-auth

        .. code-block:: bash

            DB_USER=skypilot
            DB_PASSWORD=<create a password>
            DB_INSTANCE_NAME=cloud-sql-skypilot-instance
            gcloud sql users create ${DB_USER} --instance ${DB_INSTANCE_NAME} --password ${DB_PASSWORD}


Authorize the API server to use the GCP service account
-------------------------------------------------------

In this step, we authorize the GCP service account to be used by the API server.


.. code-block:: bash

    NAMESPACE=skypilot
    kubectl create namespace ${NAMESPACE}

.. tab-set::

    .. tab-item:: IAM Auth
      :sync: iam-auth

        An IAM policy binding is created on the GCP service account to bind it to the GKE service account.

        .. code-block:: bash

            NAMESPACE=skypilot
            GCP_PROJECT_ID=<your gcp project id>
            GCP_SERVICE_ACCOUNT=skypilot-cloud-sql-access
            GKE_SERVICE_ACCOUNT=skypilot-api-sa
            gcloud iam service-accounts add-iam-policy-binding \
                --role="roles/iam.workloadIdentityUser" \
                --member="serviceAccount:${GCP_PROJECT_ID}.svc.id.goog[${NAMESPACE}/${GKE_SERVICE_ACCOUNT}]" \
                ${GCP_SERVICE_ACCOUNT}@${GCP_PROJECT_ID}.iam.gserviceaccount.com

    .. tab-item:: Password Auth
      :sync: password-auth

        A secret is created in the kubernetes cluster to store the GCP service account key.

        .. code-block:: bash

            GCP_PROJECT_ID=<your gcp project id>
            GCP_SERVICE_ACCOUNT=skypilot-cloud-sql-access
            gcloud iam service-accounts keys create gcp-key.json \
                --iam-account=${GCP_SERVICE_ACCOUNT}@${GCP_PROJECT_ID}.iam.gserviceaccount.com \
                --project=${GCP_PROJECT_ID}

        .. code-block:: bash

            NAMESPACE=skypilot
            kubectl create secret generic cloud-sql-credentials \
                --from-file=service-account-key.json=gcp-key.json -n ${NAMESPACE}

Create the database connection secret
-------------------------------------

In this step, we create a secret to store the database connection information to be used by the API server.

.. tab-set::

    .. tab-item:: IAM Auth
      :sync: iam-auth

        .. code-block:: bash

            NAMESPACE=skypilot
            DB_NAME=skypilot-db
            GCP_PROJECT_ID=<your gcp project id>
            kubectl create secret generic skypilot-db-connection-uri \
                --namespace ${NAMESPACE} \
                --from-literal connection_string="postgresql://localhost/${DB_NAME}?user=skypilot-cloud-sql-access%40${GCP_PROJECT_ID}.iam"

    .. tab-item:: Password Auth
      :sync: password-auth

        .. code-block:: bash

            NAMESPACE=skypilot
            DB_USER=skypilot
            DB_PASSWORD=<password for the 'skypilot' user>
            DB_NAME=skypilot-db
            kubectl create secret generic skypilot-db-connection-uri \
                --namespace ${NAMESPACE} \
                --from-literal connection_string=postgresql://${DB_USER}:${DB_PASSWORD}@localhost/${DB_NAME}

Deploy the SkyPilot API server
------------------------------

Replace ``<GCP_PROJECT_ID>`` and ``<REGION>`` in the following ``values.yaml`` with the corresponding values.

``values.yaml``:

.. tab-set::

    .. tab-item:: IAM Auth
      :sync: iam-auth

        .. code-block:: yaml

            apiService:
              dbConnectionSecretName: skypilot-db-connection-uri

              # config must be null when using an external database.
              # To set the config, use the web dashboard once the API server is deployed.
              config: null

            rbac:
              serviceAccountName: "skypilot-api-sa"
              serviceAccountAnnotations:
                # TODO: fill in <GCP_PROJECT_ID>
                iam.gke.io/gcp-service-account: skypilot-cloud-sql-access@<GCP_PROJECT_ID>.iam.gserviceaccount.com

            # Extra init containers to run before the api container
            extraInitContainers:
              - name: cloud-sql-proxy
                restartPolicy: Always
                # It is recommended to use the latest version of the Cloud SQL Auth Proxy
                # Make sure to update on a regular schedule!
                image: gcr.io/cloud-sql-connectors/cloud-sql-proxy:2.14.1
                args:
                  # If connecting from a VPC-native GKE cluster, you can use the
                  # following flag to have the proxy connect over private IP
                  # - "--private-ip"

                  # If you are not connecting with Automatic IAM, you can delete
                  # the following flag.
                  - "--auto-iam-authn"

                  # Enable structured logging with LogEntry format:
                  - "--structured-logs"

                  # Replace DB_PORT with the port the proxy should listen on
                  - "--port=5432"
                  # TODO: fill in <GCP_PROJECT_ID> and <REGION>
                  - "<GCP_PROJECT_ID>:<REGION>:cloud-sql-skypilot-instance"

                securityContext:
                  # The default Cloud SQL Auth Proxy image runs as the
                  # "nonroot" user and group (uid: 65532) by default.
                  runAsNonRoot: true
                # You should use resource requests/limits as a best practice to prevent
                # pods from consuming too many resources and affecting the execution of
                # other pods. You should adjust the following values based on what your
                # application needs. For details, see
                # https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/
                resources:
                  requests:
                    # The proxy's memory use scales linearly with the number of active
                    # connections. Fewer open connections will use less memory. Adjust
                    # this value based on your application's requirements.
                    memory: "2Gi"
                    # The proxy's CPU use scales linearly with the amount of IO between
                    # the database and the application. Adjust this value based on your
                    # application's requirements.
                    cpu: "1"

    .. tab-item:: Password Auth
      :sync: password-auth

        .. code-block:: yaml

            apiService:
              extraVolumes:
              - name: cloud-sql-credentials
                secret:
                  secretName: cloud-sql-credentials

              dbConnectionSecretName: skypilot-db-connection-uri

              # config must be null when using an external database.
              # To set the config, use the web dashboard once the API server is deployed.
              config: null

            # Extra init containers to run before the api container
            extraInitContainers:
              - name: cloud-sql-proxy
                restartPolicy: Always
                # It is recommended to use the latest version of the Cloud SQL Auth Proxy
                # Make sure to update on a regular schedule!
                image: gcr.io/cloud-sql-connectors/cloud-sql-proxy:2.14.1
                args:
                  # If connecting from a VPC-native GKE cluster, you can use the
                  # following flag to have the proxy connect over private IP
                  # - "--private-ip"

                  # Use service account key file for authentication
                  - "--credentials-file=/var/secrets/google/service-account-key.json"

                  # Enable structured logging with LogEntry format:
                  - "--structured-logs"

                  # Replace DB_PORT with the port the proxy should listen on
                  - "--port=5432"
                  # TODO: fill in <GCP_PROJECT_ID> and <REGION>
                  - "<GCP_PROJECT_ID>:<REGION>:cloud-sql-skypilot-instance"

                securityContext:
                  # The default Cloud SQL Auth Proxy image runs as the
                  # "nonroot" user and group (uid: 65532) by default.
                  runAsNonRoot: true
                # You should use resource requests/limits as a best practice to prevent
                # pods from consuming too many resources and affecting the execution of
                # other pods. You should adjust the following values based on what your
                # application needs. For details, see
                # https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/
                resources:
                  requests:
                    # The proxy's memory use scales linearly with the number of active
                    # connections. Fewer open connections will use less memory. Adjust
                    # this value based on your application's requirements.
                    memory: "2Gi"
                    # The proxy's CPU use scales linearly with the amount of IO between
                    # the database and the application. Adjust this value based on your
                    # application's requirements.
                    cpu: "1"
                volumeMounts:
                - name: cloud-sql-credentials
                  mountPath: /var/secrets/google
                  readOnly: true

Then run the following command to deploy the API server using helm:

.. code-block:: bash

    NAMESPACE=skypilot
    RELEASE_NAME=skypilot
    WEB_USERNAME=skypilot
    WEB_PASSWORD=<create a password>
    AUTH_STRING=$(htpasswd -nb $WEB_USERNAME $WEB_PASSWORD)
    helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
    --namespace $NAMESPACE \
    -f values.yaml \
    --set ingress.authCredentials=$AUTH_STRING
