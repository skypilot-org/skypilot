.. _docker-containers:

Using Docker Containers
=======================

SkyPilot can run a Docker container either as a containerized application, or the runtime environment for your task.

Running Containerized Application
---------------------------------

SkyPilot is capable of running containerized application directly. The Docker runtime comes pre-configured and ready for use on the default VM image provided by SkyPilot, making it convenient for executing containerized applications.

To launch a containerized application, you can directly invoke the :code:`docker run` command in the :code:`run` section of your task.

For example, to run a hugging face TGI serving container:

.. code-block:: yaml

  resources:
    accelerators: A100:1

  run: |
    docker run --gpus all --shm-size 1g -v ~/data:/data ghcr.io/huggingface/text-generation-inference --model-id lmsys/vicuna-13b-v1.5

Using Docker Containers as Runtime Environment
----------------------------------------------

When a container is used as the runtime environment, all the SkyPilot task is executed inside the container.

This means all :code:`setup` and :code:`run` commands in the YAML file will be executed in the container, and any files created by the task will be stored inside the container. The Docker container functions as an image ID within the virtual machines provided by cloud service providers.

Any GPUs assigned to the task will be automatically mapped to your Docker container and all future tasks on the cluster will also execute in the container.

Example Usage: Running a fresh Ubuntu 20.04 container as development environment

To use a Docker image as your runtime environment, set the :code:`image_id` field in the :code:`resources` section of your task YAML file to :code:`docker:<image_id>`.
For example, to use the :code:`ubuntu:20.04` image from Docker Hub:

.. code-block:: yaml

  resources:
    image_id: docker:ubuntu:20.04

  setup: |
    # Will run inside container

  run: |
    # Will run inside container

If you have a development environment set up within a Docker image, it can be utilized as the runtime environment for your tasks across various cloud providers. This proves beneficial when dealing with a complex development environment that is challenging to configure on a new virtual machine, such as dependencies on specific versions of CUDA or cuDNN.

For Docker images hosted on private registries, you can provide the registry authentication details using :ref:`task environment variables <env-vars>`:

.. code-block:: yaml

  # ecr_private_docker.yaml
  resources:
    image_id: docker:<your-user-id>.dkr.ecr.us-east-1.amazonaws.com/<your-private-image>:<tag>
    # the following shorthand is also supported:
    # image_id: docker:<your-private-image>:<tag>

  envs:
    SKYPILOT_DOCKER_USERNAME: AWS
    # SKYPILOT_DOCKER_PASSWORD: <password>
    SKYPILOT_DOCKER_SERVER: <your-user-id>.dkr.ecr.us-east-1.amazonaws.com

We suggest setting the :code:`SKYPILOT_DOCKER_PASSWORD` environment variable through the CLI (see :ref:`passing secrets <passing-secrets>`):

.. code-block:: console

  $ export SKYPILOT_DOCKER_PASSWORD=$(aws ecr get-login-password --region us-east-1)
  $ sky launch ecr_private_docker.yaml --env SKYPILOT_DOCKER_PASSWORD

Building containers remotely
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

If you are running the container as a task, the container image can also be built remotely on the cluster in the :code:`setup` phase of the task.

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
