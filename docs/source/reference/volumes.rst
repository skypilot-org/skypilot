.. _volumes:

Cloud Volumes
=============

SkyPilot supports mounting **reattachable network volumes** (e.g., GCP Persistent Disks) or
**temporary instance volumes** (e.g., GCP Local SSDs) to a SkyPilot cluster.

Currently, the following volume types are supported:

- GCP

  - Network volumes: `Persistent Disks <https://cloud.google.com/compute/docs/disks/persistent-disks>`_
  - Instance volumes: `Local SSDs <https://cloud.google.com/compute/docs/disks/local-ssd>`_ (temporary)

Use cases
---------

Use volumes for regional storage with high performance. A volume is created in a
particular region and can only be attached to instances in the same region.

Use :ref:`Cloud Buckets <sky-storage>` for object storage that works across zones, regions, and clouds.

Usage
-----

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
