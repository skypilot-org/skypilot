.. _volumes:

Cloud Volumes
=============

SkyPilot supports mounting **reattachable network volumes** (e.g., GCP Persistent Disks) or
**temporary instance volumes** (e.g., GCP Local SSDs) to a non-Kubernetes SkyPilot cluster and mounting
**PVCs** to a Kubernetes SkyPilot cluster.

Currently, the following volume types are supported:

- GCP

  - Network volumes: `Persistent Disks <https://cloud.google.com/compute/docs/disks/persistent-disks>`_
  - Instance volumes: `Local SSDs <https://cloud.google.com/compute/docs/disks/local-ssd>`_ (temporary)

- Kubernetes

  - PVCs: `Persistent Volume Claims <https://kubernetes.io/docs/concepts/storage/persistent-volumes/#persistentvolumeclaims/>`_

Use cases
---------

Use volumes for regional storage with high performance. A volume is created in a
particular region and can only be attached to instances in the same region.

Use :ref:`Cloud Buckets <sky-storage>` for object storage that works across zones, regions, and clouds.

.. _volumes-on-kubernetes:

Volumes on Kubernetes
---------------------

SkyPilot supports creating and managing PVC (Persistent Volume Claim) volumes on Kubernetes clusters.

Creating a PVC volume
~~~~~~~~~~~~~~~~~~~~~

1. Prepare a volume YAML file (e.g., ``volume.yaml``):

   .. code-block:: yaml

     name: new-pvc
     type: k8s-pvc
     infra: kubernetes  # or k8s or k8s/context
     size: 10Gi
     config:
       namespace: default  # optional
       storage_class_name: csi-mounted-fs-path-sc  # optional
       access_mode: ReadWriteMany  # optional

2. Create the volume using SkyPilot:

   .. code-block:: bash

     sky volumes apply volume.yaml

   You'll be prompted to confirm the creation:

   .. code-block:: text

     Proceed to create volume 'new-pvc'? [Y/n]: Y
     Creating PVC: new-pvc-73ec42f2-5c6c4e

3. Mount the volume in your task YAML:

   .. code-block:: yaml

     resources:
       cpus: 4+
       num_nodes: 2

     volumes:
       /mnt/data: new-pvc

Managing PVC volumes
~~~~~~~~~~~~~~~~~~~~

List all volumes:

.. code-block:: bash

  sky volumes ls

Example output:

.. code-block:: text

  NAME     TYPE  CONTEXT          NAMESPACE  SIZE  USER_HASH  WORKSPACE  LAUNCHED     LAST_ATTACHED        STATUS   LAST_USE
  new-pvc  pvc   nebius-mk8s-vol  default    1Gi   73ec42f2   default    56 secs ago  2025-06-23 14:42:17  IN_USE   sky volumes apply --name ...

Delete a volume:

.. code-block:: bash

  sky volumes delete new-pvc

You'll be prompted to confirm the deletion:

.. code-block:: text

  Deleting 1 volume: new-pvc. Proceed? [Y/n]:
  Deleting PVC: new-pvc-73ec42f2-5c6c4e

.. note::
  - Both the SkyPilot volume resource and the underlying Kubernetes PVC will be deleted.
  - If the volume is in use, it will be marked as ``IN_USE`` and cannot be deleted.

Volumes on GCP
--------------

Volumes are specified using the :ref:`file_mounts <yaml-spec-file-mounts>` field in a SkyPilot task.

There are three ways to mount volumes:

1. Mount an existing volume
2. Create and mount a new network volume (reattachable)
3. Create and mount a new instance volume (temporary)

.. tab-set::

    .. tab-item:: Mount existing volume
        :sync: existing-volume-tab

        To mount an existing volume:

        1. Ensure the volume exists
        2. Specify the volume name using ``name: volume-name``
        3. Specify the region or zone in the resources section to match the volume's location

        .. code-block:: yaml

          file_mounts:
            /mnt/path:
              name: volume-name
              store: volume
              persistent: true

          resources:
            # Must specify cloud, and region or zone.
            # These need to match the volume's location.
            cloud: gcp
            region: us-central1
            # zone: us-central1-a

    .. tab-item:: Create network volume
        :sync: new-network-volume-tab

        To create and mount a new network volume:

        1. Specify the volume name using ``name: volume-name``
        2. Specify the desired volume configuration (``disk_size``, ``disk_tier``, etc.)

        .. code-block:: yaml

          file_mounts:
            /mnt/path:
              name: new-volume
              store: volume
              persistent: true  # If false, delete the volume when cluster is downed.
              config:
                disk_size: 100  # GiB.

          resources:
            # Must specify cloud, and region or zone.
            cloud: gcp
            region: us-central1
            # zone: us-central1-a

        SkyPilot will automatically create and mount the volume to the specified path.

    .. tab-item:: Create instance volume
        :sync: new-instance-volume-tab

        To create and mount a new instance volume (temporary disk; will be lost when the cluster is stopped or terminated):

        .. code-block:: yaml

          file_mounts:
            /mnt/path:
              store: volume
              config:
                storage_type: instance

          resources:
            # Must specify cloud.
            cloud: gcp

        Note that the ``name`` and ``config.disk_size`` fields are unsupported,
        and will be ignored even if specified.

        SkyPilot will automatically create and mount the volume to the specified path.


Configuration options
~~~~~~~~~~~~~~~~~~~~~

Here's a complete example showing all available configuration options:

.. code-block:: yaml

  file_mounts:
    /mnt/path:
      store: volume

      # Name of the volume to mount.
      #
      # Required for network volume, ignored for instance volume.  If the volume
      # doesn't exist in the specified region, it will be created in the region.
      name: volume-name

      # Source local path.
      #
      # Do not set if no need to sync data from local to volume.  If specified,
      # the data will be synced to the /mnt/path/data directory.
      source: /local/path

      # If set to false, the volume will be deleted when the cluster is downed.
      # Default: false
      persistent: false

      config:
        # Size of the volume in GiB. Ignored for instance volumes.
        disk_size: 100

        # Type of the volume, either 'network' or 'instance'.
        # Default: 'network'
        storage_type: network

        # Tier of the volume, same as `resources.disk_tier`.
        # Default: best
        disk_tier: best

        # Attach mode, either 'read_write' or 'read_only'.
        # Default: read_write
        attach_mode: read_write

See :ref:`YAML spec for volumes <yaml-spec-volumes>` for more details.
