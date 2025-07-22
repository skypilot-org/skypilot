Deploying a SkyPilot API Server on GKE backed by GCP Cloud SQL
==============================================================

In this example, a SkyPilot API server is deployed on a GKE cluster with a persistent database backed by GCP Cloud SQL.

Prerequisites
-------------

* `A GKE cluster <https://cloud.google.com/kubernetes-engine/docs/how-to/creating-a-zonal-cluster>`_
* `Helm <https://helm.sh/docs/intro/install/>`_
* `kubectl <https://kubernetes.io/docs/tasks/tools/#kubectl>`_
* `gcloud CLI <https://cloud.google.com/sdk/docs/install>`_
* Access to GCP console

Step 1: Create a cloud SQL instance
-----------------------------------

- Go to the `Cloud SQL console <https://console.cloud.google.com/sql/instances>`_
- Click on "Create instance"
- Select "PostgreSQL" as the database engine
- Set the instance ID to ``cloud-sql-skypilot-instance``
- Set the password for the ``postgres`` user.
- Select the region (and zone if applicable) where you want to create the instance. The region / zone of the database should match that of the GKE cluster.
- Click on "Create Instance"

Step 2: Configure the cloud SQL instance
----------------------------------------

Once the instance is created, we need to configure the instance to create a user and a database for SkyPilot API server.

To create a user, use `gcloud CLI <https://cloud.google.com/sdk/docs/install>`_ to run the following command:

.. code-block:: bash

    DB_USER=skypilot
    DB_PASSWORD=<create a password>
    DB_INSTANCE_NAME=cloud-sql-skypilot-instance
    gcloud sql users create ${DB_USER} --instance ${DB_INSTANCE_NAME} --password ${DB_PASSWORD}

To create a database, use `gcloud CLI <https://cloud.google.com/sdk/docs/install>`_ to run the following command:

.. code-block:: bash

    DB_NAME=skypilot-db
    DB_INSTANCE_NAME=cloud-sql-skypilot-instance
    gcloud sql databases create ${DB_NAME} --instance ${DB_INSTANCE_NAME}

Step 3: Create a service account to give access to the cloud SQL instance
-------------------------------------------------------------------------

In this step, a service account is created to give the API server access to the Cloud SQL instance.

- Go to the `Service Accounts <https://console.cloud.google.com/iam-admin/serviceaccounts>`_ page of the ``IAM and Admin`` console
- Click on "Create Service Account"
- Set the service account name and ID to ``skypilot-cloud-sql-access``
- Click on "Create and Continue" to move to the Permissions page.
- Click on "Select a member" and add ``Cloud SQL Client`` as the role.
- Click on "Continue", then "Done" to create the service account.

Step 4: Add in IAM policy binding to the service account
--------------------------------------------------------

In this step, an IAM policy binding is created on the GCP service account to bind it to the GKE service account.

This step gives the GKE service account access to the GCP service account.

.. code-block:: bash

    NAMESPACE=skypilot
    GCP_PROJECT_ID=<your gcp project id>
    GCP_SERVICE_ACCOUNT=skypilot-cloud-sql-access
    GKE_SERVICE_ACCOUNT=skypilot-api-sa
    gcloud iam service-accounts add-iam-policy-binding \
        --role="roles/iam.workloadIdentityUser" \
        --member="serviceAccount:${GCP_PROJECT_ID}.svc.id.goog[${NAMESPACE}/${GKE_SERVICE_ACCOUNT}]" \
        ${GCP_SERVICE_ACCOUNT}@${GCP_PROJECT_ID}.iam.gserviceaccount.com

Step 5: Set up the GKE cluster to use the GCP service account
-------------------------------------------------------------

In this step, the GKE cluster is configured so the API server can use the GCP service account.


.. code-block:: bash

    NAMESPACE=skypilot
    kubectl create namespace ${NAMESPACE}

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


Step 6: Deploy the SkyPilot API server
--------------------------------------

Use the following values.yaml to deploy the SkyPilot API server.


``values.yaml``:

.. code-block:: yaml

    apiService:
      extraVolumes:
      - name: cloud-sql-credentials
        secret:
          secretName: cloud-sql-credentials

      config: |
        db: postgresql://$DB_USER:$DB_PASSWORD@localhost/$DB_NAME

    rbac:
      serviceAccountName: $GKE_SERVICE_ACCOUNT
      serviceAccountAnnotations:
        iam.gke.io/gcp-service-account: $GCP_SERVICE_ACCOUNT@$GCP_PROJECT_ID.iam.gserviceaccount.com

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
          # - "--auto-iam-authn"

          # Use service account key file for authentication
          - "--credentials-file=/var/secrets/google/service-account-key.json"

          # Enable structured logging with LogEntry format:
          - "--structured-logs"

          # Replace DB_PORT with the port the proxy should listen on
          - "--port=5432"
          - "$GCP_PROJECT_ID:$REGION:$DB_INSTANCE_NAME"

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

Replace ``$DB_USER``, ``$DB_PASSWORD``, ``$DB_NAME``, ``$GCP_SERVICE_ACCOUNT``, ``$GKE_SERVICE_ACCOUNT``,  ``$DB_INSTANCE_NAME``, ``$GCP_PROJECT_ID``, ``$REGION`` with the corresponding values.

For reference, the following values are used in the example:

.. code-block:: bash

    DB_USER=skypilot
    DB_PASSWORD=<password for the 'skypilot' user>
    DB_NAME=skypilot-db
    GCP_SERVICE_ACCOUNT=skypilot-cloud-sql-access
    GKE_SERVICE_ACCOUNT=skypilot-api-sa
    DB_INSTANCE_NAME=cloud-sql-skypilot-instance
    GCP_PROJECT_ID=<your gcp project id>
    REGION=<region of the GKE cluster>


For your convenience, here is a ``values.yaml`` file with ``$DB_USER``, ``$DB_NAME``, ``$GCP_SERVICE_ACCOUNT``, ``$GKE_SERVICE_ACCOUNT``, and ``$DB_INSTANCE_NAME`` filled in.

.. dropdown:: ``values.yaml`` file with populated values.

    .. code-block:: yaml

        apiService:
          extraVolumes:
          - name: cloud-sql-credentials
            secret:
              secretName: cloud-sql-credentials

          config: |
            db: postgresql://skypilot:$DB_PASSWORD@localhost/skypilot-db

        rbac:
          serviceAccountName: "skypilot-api-sa"
          serviceAccountAnnotations:
            iam.gke.io/gcp-service-account: skypilot-cloud-sql-access@$GCP_PROJECT_ID.iam.gserviceaccount.com

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
              # - "--auto-iam-authn"

              # Use service account key file for authentication
              - "--credentials-file=/var/secrets/google/service-account-key.json"

              # Enable structured logging with LogEntry format:
              - "--structured-logs"

              # Replace DB_PORT with the port the proxy should listen on
              - "--port=5432"
              - "$GCP_PROJECT_ID:$REGION:cloud-sql-skypilot-instance"

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
