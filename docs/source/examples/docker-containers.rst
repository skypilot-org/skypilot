.. _docker-containers:

Running Docker Containers
=========================

SkyPilot can run docker containers as tasks. Docker runtime is configured and ready for use on the default VM image used by SkyPilot.

To run a container, you can directly invoke the :code:`docker run` command in the :code:`run` section of your task.

For example, to run a GPU-accelerated container that prints the output of :code:`nvidia-smi`:

.. code-block:: yaml

  resources:
    accelerators: V100:1

  run: |
    docker run --rm --gpus all nvidia/cuda:11.6.2-base-ubuntu20.04 nvidia-smi

Building containers remotely
----------------------------

If required, the container image can also be built remotely on the cluster in the :code:`setup` phase of the task.

The :code:`echo_app` `example <https://github.com/skypilot-org/skypilot/tree/master/examples/docker>`_ provides an example on how to do this:

.. code-block:: yaml

  file_mounts:
    /inputs: ./echo_app  # Input to application
    /echo_app: ./echo_app  # Contains the Dockerfile and build context
    /outputs:  # Output to be written directly to S3 bucket
      name: # Set unique bucket name here
      store: s3
      mode: MOUNT

  setup: |
    # Build docker image. If pushed to a registry, can also do docker pull here
    docker build -t echo:v0 /echo_app

  run: |
    docker run --rm \
    --volume="/inputs:/inputs:ro" \
    --volume="/outputs:/outputs:rw" \
    echo:v0 \
    /inputs/README.md /outputs/output.txt

In this example, the Dockerfile and build context are contained in :code:`./echo_app`.
The :code:`setup` phase of the task builds the image, and the :code:`run` phase runs the container.
The inputs to the app are copied to SkyPilot using :code:`file_mounts` and mounted into the container using docker volume mounts (:code:`--volume` flag).
The output of the app produced at :code:`/outputs` path in the container is also volume mounted to :code:`/outputs` on the VM, which gets directly written to a S3 bucket through SkyPilot Storage mounting.

Our GitHub repository has more examples, including running `Detectron2 in a Docker container <https://github.com/skypilot-org/skypilot/blob/master/examples/detectron2_docker.yaml>`_ via SkyPilot.