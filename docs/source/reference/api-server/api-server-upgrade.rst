.. _sky-api-server-upgrade:

Upgrades and High Availability
==============================

This page covers how to keep a remote SkyPilot API server resilient and up to date:

* :ref:`api-server-ha` — back the API server with an external PostgreSQL database for production deployments
* :ref:`sky-api-server-helm-upgrade` — upgrade a Helm-deployed API server gracefully
* :ref:`sky-api-server-vm-upgrade` — upgrade an API server deployed on a VM

.. _api-server-ha:

High availability
-----------------

The SkyPilot API server can be configured for high availability by making it fully stateless — backing it with an external PostgreSQL database decouples API server state from the API server pod, allowing the pod to be restarted, rescheduled, or upgraded (including via :ref:`rolling updates <sky-api-server-upgrade-strategy>`) without losing state.

.. note::

    Multi-replica API server deployments are not supported in open-source SkyPilot.

.. tip::

    **Scaling SkyPilot beyond 20 users or 1,000 GPUs, or need multi-replica high availability?** We would love to talk to you. SkyPilot has been supporting teams with 200+ users and 10,000+ GPUs with high availability and up to 10× faster performance — `sign up here <https://forms.gle/d2q9AVYeMA3eaKXW6>`_.

.. _api-server-persistence-db:

Back the API server with a persistent database
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The API server can optionally be configured with a PostgreSQL database to persist state. It can be an externally managed database.

If a persistent DB is not specified, the API server uses a Kubernetes persistent volume to persist state.

.. note::

  Database configuration must be set in the Helm deployment.

Configure PostgreSQL during the first Helm deployment using one of the two options below.

**Option 1: Set the DB connection URI in helm values**

Set :ref:`apiService.dbConnectionString <helm-values-apiService-dbConnectionString>` to ``postgresql://<username>:<password>@<host>:<port>/<database>`` in the helm values:

.. code-block:: bash

    # --reuse-values keeps the Helm chart values set in the previous step
    helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
      --namespace $NAMESPACE \
      --reuse-values \
      --set apiService.dbConnectionString=postgresql://<username>:<password>@<host>:<port>/<database>

**Option 2: Set the DB connection URI via Kubernetes secret**

(available on nightly version 20250626 and later)

Create a Kubernetes secret that contains the DB connection URI:

.. code-block:: bash

    kubectl create secret generic skypilot-db-connection-uri \
      --namespace $NAMESPACE \
      --from-literal connection_string=postgresql://<username>:<password>@<host>:<port>/<database>

When installing or upgrading the Helm chart, set the ``dbConnectionUri`` to the secret name:

.. code-block:: bash

    helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
      --namespace $NAMESPACE \
      --reuse-values \
      --set apiService.dbConnectionSecretName=skypilot-db-connection-uri

You can also directly set this value in the ``values.yaml`` file, e.g.:

.. code-block:: yaml

    apiService:
      dbConnectionSecretName: skypilot-db-connection-uri

.. note::

    Once :ref:`apiService.dbConnectionString <helm-values-apiService-dbConnectionString>` or :ref:`apiService.dbConnectionSecretName <helm-values-apiService-dbConnectionSecretName>` is specified, no other SkyPilot configuration can be specified in the helm chart. That is, :ref:`apiService.config <helm-values-apiService-config>` must be ``null``. To set any other SkyPilot configuration, see :ref:`sky-api-server-config`. To move an existing API server that was deployed without a database, including its config, see :ref:`api-server-migrate-sqlite-to-postgres`.

.. _api-server-migrate-sqlite-to-postgres:

Migrate an existing API server to PostgreSQL
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

An API server deployed without a database keeps its state in SQLite files on its persistent volume. To move that state (users, clusters, managed jobs, services, recipes, and the API server config) to PostgreSQL, run the ``sky.utils.db.migrate_sqlite_to_postgres`` module once with the API server stopped, then switch the Helm deployment to the database.

The migration:

* refuses a target database that already holds SkyPilot data, so start from a new, empty database;
* only reads the SQLite files, so rolling back is pointing the API server back at them;
* creates the schema with the same SkyPilot version as the API server, so run it with the API server's image;
* copies all tables in a single transaction and checks the row counts, so a failed run leaves the target empty;
* stores the API server config (``~/.sky/config.yaml``, which the API server initialized from :ref:`apiService.config <helm-values-apiService-config>`) in the database, where an API server backed by PostgreSQL reads it from.

What to expect:

* The API server is down from the first step until the last. Managed jobs, clusters, and service replicas keep running, and the API server picks them up once it is back.
* With :ref:`consolidation mode <jobs-consolidation-mode>`, the managed job and service controllers run inside the API server, so service endpoints are unreachable while the API server is down.
* The API request history is not migrated.
* The migration itself is quick. For example, a state of about 1.5 GB (about 340,000 rows across 24 tables) took about 2 minutes to copy into Amazon Aurora PostgreSQL.

The steps below use the following variables:

.. code-block:: bash

    RELEASE_NAME=skypilot  # Helm release name of the API server
    NAMESPACE=skypilot     # Namespace of the API server

**Step 1: Stop the API server**

The managed job and service controllers in the API server keep updating job, cluster, and service statuses in SQLite. A copy taken while they run misses the updates made after it, and a job that finished in that gap would be running again in PostgreSQL, where recovery can relaunch it. Scale the API server to zero:

.. code-block:: bash

    helm upgrade $RELEASE_NAME skypilot/skypilot-nightly --devel \
      --namespace $NAMESPACE \
      --reuse-values \
      --set apiService.replicas=0

    kubectl get pods -n $NAMESPACE -l app=${RELEASE_NAME}-api

Wait until the API server pod is gone. If the release is managed by Argo CD with self-heal enabled, set ``apiService.replicas: 0`` in the values in Git instead, as self-healing reverts a manual scale-down.

**Step 2: Back up the state volume**

The migration does not modify the SQLite files, but take a backup before you start, e.g. a `volume snapshot <https://kubernetes.io/docs/concepts/storage/volume-snapshots/>`_ of the ``${RELEASE_NAME}-state`` persistent volume claim, or a snapshot with your cloud provider.

**Step 3: Run the migration**

Store the connection URI of the new, empty database in a secret. The same secret is used by the API server in the next step:

.. code-block:: bash

    kubectl create secret generic skypilot-db-connection-uri \
      --namespace $NAMESPACE \
      --from-literal connection_string=postgresql://<username>:<password>@<host>:<port>/<database>

Start a pod with the API server's image that mounts the API server's state volume. The volume is mounted writable because SQLite needs to create its shared-memory file to read a database in WAL mode; the migration does not write to the SQLite files.

.. code-block:: bash

    IMAGE=$(kubectl get deployment ${RELEASE_NAME}-api-server -n $NAMESPACE \
      -o jsonpath='{.spec.template.spec.containers[?(@.name=="skypilot-api")].image}')

    kubectl apply -n $NAMESPACE -f - <<EOF
    apiVersion: v1
    kind: Pod
    metadata:
      name: skypilot-db-migration
    spec:
      restartPolicy: Never
      containers:
      - name: migration
        image: $IMAGE
        command: ["sleep", "infinity"]
        env:
        - name: SKYPILOT_DB_CONNECTION_URI
          valueFrom:
            secretKeyRef:
              name: skypilot-db-connection-uri
              key: connection_string
        volumeMounts:
        - name: state-volume
          mountPath: /root/.sky
          subPath: .sky
      volumes:
      - name: state-volume
        persistentVolumeClaim:
          claimName: ${RELEASE_NAME}-state
    EOF

    kubectl wait pod/skypilot-db-migration -n $NAMESPACE --for=condition=Ready

.. note::

    If you set ``storage.existingClaim``, use that claim instead of ``${RELEASE_NAME}-state``. Add the API server's ``nodeSelector``, ``tolerations``, or image pull secrets to the pod if your cluster requires them.

Run the migration:

.. code-block:: bash

    kubectl exec -n $NAMESPACE skypilot-db-migration -- \
      python -m sky.utils.db.migrate_sqlite_to_postgres

It prints the number of rows copied per table. To migrate a config other than ``~/.sky/config.yaml``, pass ``--config <path>``. If it fails, the database is left empty and the API server can be started again on SQLite (see :ref:`rollback <api-server-migrate-sqlite-to-postgres-rollback>`).

An API server backed by PostgreSQL reads its config from the database and does not start if ``~/.sky/config.yaml`` on its volume still holds the old config. Move the file aside, keeping it for a rollback, and delete the pod:

.. code-block:: bash

    kubectl exec -n $NAMESPACE skypilot-db-migration -- \
      mv /root/.sky/config.yaml /root/.sky/config.yaml.sqlite
    kubectl delete pod skypilot-db-migration -n $NAMESPACE

**Step 4: Start the API server on PostgreSQL**

Point the API server at the database, unset :ref:`apiService.config <helm-values-apiService-config>` (it must be ``null`` with a database; the config was migrated into the database), and scale the API server back up:

.. code-block:: bash

    helm upgrade $RELEASE_NAME skypilot/skypilot-nightly --devel \
      --namespace $NAMESPACE \
      --reuse-values \
      --set apiService.dbConnectionSecretName=skypilot-db-connection-uri \
      --set apiService.config=null \
      --set apiService.replicas=1

With GitOps, make the same change to the values in Git.

**Step 5: Verify**

Check that the state was carried over and that new work runs:

.. code-block:: bash

    sky api info
    sky status
    sky jobs queue
    sky serve status
    sky jobs launch -y 'echo hello'

New managed job IDs continue after the largest migrated one. Further config changes are made through the :ref:`dashboard or the API <sky-api-server-config>`.

.. tip::

    Only the API server's own processes pool database connections by default. Other processes, such as the managed job and service controllers in :ref:`consolidation mode <jobs-consolidation-mode>`, open a new connection for every query, which adds a network round trip and a TLS handshake each time with a remote database. If ``sky serve status`` or ``sky jobs queue`` is slow after the migration, set ``SKYPILOT_SERVER_DB_CONNECTION_POOL_SIZE`` (e.g. ``2``) in :ref:`apiService.extraEnvs <helm-values-apiService-extraEnvs>` so that every process reuses its connections.

.. _api-server-migrate-sqlite-to-postgres-rollback:

**Rollback**

The SQLite files are left as they were, so to go back to SQLite:

1. Scale the API server to zero as in step 1.
2. Restore the config file. In a pod that mounts the state volume as in step 3, run ``mv /root/.sky/config.yaml.sqlite /root/.sky/config.yaml``.
3. Revert the Helm values: unset ``apiService.dbConnectionSecretName``, restore ``apiService.config``, and set ``apiService.replicas`` back to ``1``.

Anything written while the API server ran on PostgreSQL is not carried back. To retry the migration, recreate the database so that it is empty.

.. _sky-api-server-helm-upgrade:

Upgrade API server deployed with Helm
-------------------------------------

With :ref:`Helm deployement <sky-api-server-deploy>`, it is possible to :ref:`upgrade the SkyPilot API server gracefully<sky-api-server-graceful-upgrade>` without causing client-side error with the steps below.

Step 1: Prepare an upgrade
~~~~~~~~~~~~~~~~~~~~~~~~~~

1. Find the version to use in SkyPilot `nightly build <https://pypi.org/project/skypilot-nightly/#history>`_.
2. Update SkyPilot helm repository to the latest version:

.. code-block:: bash

    helm repo update skypilot

3. Prepare versioning environment variables.  ``NAMESPACE`` and ``RELEASE_NAME`` should be set to the currently installed namespace and release:

.. code-block:: bash

    NAMESPACE=skypilot # TODO: change to your installed namespace
    RELEASE_NAME=skypilot # TODO: change to your installed release name
    VERSION=1.0.0-dev20250410 # TODO: change to the version you want to upgrade to
    IMAGE_REPO=berkeleyskypilot/skypilot-nightly

Step 2: Upgrade the API server and clients
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Upgrade the clients:

.. code-block:: bash

    pip install -U skypilot-nightly==${VERSION}

Upgrade the API server:

.. code-block:: bash

    # --reuse-values is critical to keep the values set in the previous installation steps.
    helm upgrade -n $NAMESPACE $RELEASE_NAME skypilot/skypilot-nightly --devel --reuse-values \
      --set apiService.image=${IMAGE_REPO}:${VERSION}

When the API server is being upgraded, the SkyPilot CLI and Python SDK will automatically retry requests until the new version of the API server is started. So the upgrade process is graceful if the new version of the API server does not break :ref:`API compatbility<sky-api-server-api-compatibility>`. For more details, refer to :ref:`sky-api-server-graceful-upgrade`.

Optionally, you can watch the upgrade progress with:

.. code-block:: console

    $ kubectl get pod --namespace $NAMESPACE -l app=${RELEASE_NAME}-api --watch
    NAME                                       READY   STATUS            RESTARTS   AGE
    skypilot-demo-api-server-cf4896bdf-62c96   0/1     Init:0/2          0          7s
    skypilot-demo-api-server-cf4896bdf-62c96   0/1     Init:1/2          0          24s
    skypilot-demo-api-server-cf4896bdf-62c96   0/1     PodInitializing   0          26s
    skypilot-demo-api-server-cf4896bdf-62c96   0/1     Running           0          27s
    skypilot-demo-api-server-cf4896bdf-62c96   1/1     Running           0          50s

The upgraded API server is ready to serve requests after the pod becomes running and the ``READY`` column shows ``1/1``.

.. note::

    ``apiService.config`` will be IGNORED during an upgrade. To update your SkyPilot config, see :ref:`here <sky-api-server-config>`.


Step 3: Verify the upgrade
~~~~~~~~~~~~~~~~~~~~~~~~~~

Verify the API server is able to serve requests and the version is consistent with the version you upgraded to:

.. code-block:: console

    $ sky api info
    Using SkyPilot API server: <ENDPOINT>
    ├── Status: healthy, commit: 022a5c3ffe258f365764b03cb20fac70934f5a60, version: 1.0.0.dev20250410
    └── User: aclice (abcd1234)

If possible, you can also trigger your pipelines that depend on the API server to verify there is no compatibility issue after the upgrade.

.. _sky-api-server-vm-upgrade:

Upgrade the API server deployed on VM
-------------------------------------

.. note::

    VM deployment does not offer graceful upgrade. We recommend the Helm deployment :ref:`sky-api-server-deploy` in production environments. The following is a workaround for upgrading SkyPilot API server in VM deployments.

Suppose the cluster name of the API server is ``api-server`` (which is used in the :ref:`sky-api-server-cloud-deploy` guide), you can upgrade the API server with the following steps:

1. Get the version to upgrade to from SkyPilot `nightly build <https://pypi.org/project/skypilot-nightly/#history>`_.

2. Switch to the original API server endpoint used to launch the cloud VM for API server. It is usually locally started when you ran ``sky launch -c api-server skypilot-api-server.yaml`` in :ref:`sky-api-server-cloud-deploy` guide:

.. code-block:: bash

    # Replace http://localhost:46580 with the real API server endpoint if you were not using the local API server to launch the API server VM instance.
    sky api login -e http://localhost:46580

3. Check the API server VM instance is ``UP``:

.. code-block:: console

    $ sky status api-server
    Clusters
    NAME        LAUNCHED     RESOURCES                                                                  STATUS  AUTOSTOP  COMMAND
    api-server  41 mins ago  1x AWS(c6i.2xlarge, image_id={'us-east-1': 'docker:berkeleyskypilot/sk...  UP      -         sky exec api-server pip i...

4. Upgrade the clients:

.. code-block:: bash

    pip install -U skypilot-nightly==${VERSION}

.. note::

    After upgrading the clients, they should not be used until the API server is upgraded to the new version.

5. Upgrade the SkyPilot on the VM and restart the API server:

.. note::

    Upgrading and restarting the API server will interrupt all pending and running requests.

.. code-block:: bash

    sky exec api-server "pip install -U skypilot-nightly[all] && sky api stop && sky api start --deploy"
    # Alternatively, you can also upgrade to a specific version with:
    sky exec api-server "pip install -U skypilot-nightly[all]==${VERSION} && sky api stop && sky api start --deploy"

6. Switch back to the remote API server:

.. code-block:: bash

    ENDPOINT=$(sky status --endpoint api-server)
    sky api login -e $ENDPOINT

7. Verify the API server is running and the version is consistent with the version you upgraded to:

.. code-block:: console

    $ sky api info
    Using SkyPilot API server: <ENDPOINT>
    ├── Status: healthy, commit: 022a5c3ffe258f365764b03cb20fac70934f5a60, version: 1.0.0.dev20250410
    └── User: aclice (abcd1234)

.. _sky-api-server-graceful-upgrade:

Graceful upgrade
----------------

A server can be gracefully upgraded when the following conditions are met:

* :ref:`Helm deployment<sky-api-server-deploy>` is used;
* Versions before and after upgrade are :ref:`compatible<sky-api-server-api-compatibility>`;

Behavior when the API server is being upgraded:

* For critical ongoing requests (e.g., launching a cluster), it waits for them to finish with a timeout.
* For non-critical ongoing requests (e.g., log tailing), it cancels them and returns an error to ask the client to retry.
* For new requests, it returns an error to ask the client to retry. New requests will be served when the new version of the API server is ready.

To further reduce the waiting time during upgrade, you can use :ref:`rolling update for the API server<sky-api-server-upgrade-strategy>`.

SkyPilot Python SDK and CLI will automatically retry until the new version of API server starts, and ongoing requests (e.g., log tailing) will automatically resume:

.. image:: https://i.imgur.com/jUjXu0J.gif
  :alt: GIF for graceful upgrade
  :align: center

To ensure that all the regular critical requests can complete within the timeout, you can adjust the timeout by setting :ref:`apiService.terminationGracePeriodSeconds <helm-values-apiService-terminationGracePeriodSeconds>` in helm values based on your workload, e.g.:

.. code-block:: bash

    helm upgrade -n $NAMESPACE $RELEASE_NAME skypilot/skypilot-nightly --devel --reuse-values \
      --set apiService.terminationGracePeriodSeconds=300

.. _sky-api-server-upgrade-strategy:

Upgrade strategy
----------------

By default, the API server is upgraded with the ``Recreate`` strategy, which introduces waiting time for new requests during upgrade. To eliminate the waiting time, you can upgrade the API server with the ``RollingUpdate`` strategy.

.. note::

    ``RollingUpdate`` is an experimental feature. There is a known limitation that some running commands might fail when the old version of the API server gets removed from the ingress backend. It is recommended to schedule the upgrade during a maintenance window.

.. warning::

    **Managed jobs and local file mounts:** Local ``file_mounts`` and ``workdir`` for managed jobs are stored on the pod's ephemeral filesystem and will be lost when the old pod is replaced during a rolling update. To avoid this:

    - Enable :ref:`persistent storage <helm-values-storage-enabled>` with a ``ReadWriteMany`` (RWX) PVC so both pods can access the files during the transition.
    - Alternatively, use :ref:`cloud buckets <sky-storage>`, :ref:`volumes <volumes-on-kubernetes>`, or :ref:`git <sync-code-and-project-files-git>` instead of local paths; or set :ref:`jobs.bucket <config-yaml-jobs-bucket>` to redirect all local file uploads to a cloud bucket.

    This does not apply if you are using a :ref:`remote jobs controller <jobs-controller-remote>`.

The following table compares the two upgrade strategies:

.. list-table:: Upgrade Strategy Comparison
   :widths: 25 35 40
   :header-rows: 1

   * - Aspect
     - ``Recreate``
     - ``RollingUpdate``
   * - **Availability**
     - Brief downtime during upgrade
     - Zero downtime
   * - **Request Handling**
     - New requests wait until upgrade completes
     - New requests served continuously by available replicas
   * - **Database Requirements**
     - Can use local storage (SQLite)
     - Must use external persistent database
   * - **Resource Usage During Upgrade**
     - Terminates old API server pod, then starts new one
     - Starts new API server pod, then terminates old one
   * - **Use Cases**
     - Development environments, simple setups
     - Production environments requiring high availability

To use the ``RollingUpdate`` strategy, you need to:

* :ref:`Back the API server with a persistent database <api-server-persistence-db>`;
* Disable local peristence by setting :ref:`storage.enabled <helm-values-storage-enabled>` to ``false``;
* Set :ref:`apiService.upgradeStrategy <helm-values-apiService-upgradeStrategy>` to ``RollingUpdate``;
* Keep the ingress enabled (:ref:`ingress.enabled <helm-values-ingress-enabled>` is ``true`` by default) or :ref:`configure your ingress to improve the availability during upgrade <sky-api-server-rolling-update-ingress>`;

Here's an example of deploying the API server with the ``RollingUpdate`` strategy:

.. code-block:: bash

    helm upgrade --install -n $NAMESPACE $RELEASE_NAME skypilot/skypilot-nightly --devel --reuse-values \
      --set apiService.upgradeStrategy=RollingUpdate \
      --set storage.enabled=false \
      --set apiService.dbConnectionSecretName=my-db-secret

.. _sky-api-server-rolling-update-ingress:

Ingress config
--------------

The SkyPilot helm chart automatically configures the ingress resource to achieve higher availability during upgrade. If you are managing the ingress resource outside of the SkyPilot helm chart, refer to the following snippet to improve the availability during upgrades:

.. dropdown:: Example ingress based on nginx-ingress-controller

    .. code-block:: yaml

        apiVersion: networking.k8s.io/v1
        kind: Ingress
        metadata:
          name: your-ingress-name
          annotations:
            # Enable session affinity to route the requests of the same client to the same pod during upgrade.
            # Without session affinity, the chance that requests fail during upgrade would be higher.
            nginx.ingress.kubernetes.io/affinity: "cookie"
            nginx.ingress.kubernetes.io/session-cookie-name: "SKYPILOT_ROUTEID"
            nginx.ingress.kubernetes.io/affinity-mode: "persistent"
            nginx.ingress.kubernetes.io/session-cookie-change-on-failure: "true"

.. _sky-api-server-api-compatibility:

API compatibility
-----------------

Starting from ``0.10.0``, SkyPilot guarantees API compatibility between adjacent minor versions, which makes graceful upgrades across minor versions possible. 

For example, assuming ``0.11.0`` is released, the following table shows one possible upgrade sequence that can upgrade the API server and clients from ``0.10.0`` to ``0.11.0`` without breaking API compatibility:

.. list-table:: Upgrade across minor versions
   :widths: 25 25 10 35
   :header-rows: 1

   * - ``Client``
     - ``Server``
     - ``Compatible``
     - ``Notes``
   * - ``0.10.0``
     - ``0.10.0``
     - ``Yes``
     - Initial state
   * - ``0.10.0``
     - ``0.11.0``
     - ``Yes``
     - Upgrade the API server first
   * - ``0.11.0``
     - ``0.11.0``
     - ``Yes``
     - Gradually upgrade all clients

When the client and server are running on different minor versions, SkyPilot CLI will print an upgrade hint as a reminder to upgrade the client:

.. code-block:: console

    $ sky status
    The SkyPilot API server is running in version X, which is newer than your client version Y. The compatibility for your current version might be dropped in the next server upgrade.
    Consider upgrading your client with:
    pip install -U skypilot==X.X.X

For a nightly build, its API compatibility is equivalent to its previous minor version, e.g., all nightly builds after ``0.10.0`` and before ``0.11.0`` have the same API compatibility guarantee as ``0.10.0``.
