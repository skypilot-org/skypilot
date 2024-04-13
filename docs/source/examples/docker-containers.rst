.. _docker-containers:

Using Docker Containers
=======================

SkyPilot can run a container either as a task, or as the runtime environment of a cluster.

* If the container image is invocable / has an entrypoint: run it :ref:`as a task <docker-containers-as-tasks>`.
* If the container image is to be used as a runtime environment (e.g., ``ubuntu``, ``nvcr.io/nvidia/pytorch:23.10-py3``, etc.) and if you have extra commands to run inside the container: run it :ref:`as a runtime environment <docker-containers-as-runtime-environments>`.

.. _docker-containers-as-tasks:

Running Containers as Tasks
---------------------------

SkyPilot can run containerized applications directly as regular tasks. The default VM images provided by SkyPilot already have the Docker runtime pre-configured.

To launch a containerized application, you can directly invoke :code:`docker run` in the :code:`run` section of your task.

For example, to run a HuggingFace TGI serving container:

.. code-block:: yaml

  resources:
    accelerators: A100:1

  run: |
    docker run --gpus all --shm-size 1g -v ~/data:/data \
      ghcr.io/huggingface/text-generation-inference \
      --model-id lmsys/vicuna-13b-v1.5

    # NOTE: Uncommon to have any commands after the above.
    # `docker run` is blocking, so any commands after it
    # will NOT be run inside the container.

Private Registries
^^^^^^^^^^^^^^^^^^

When using this mode, to access Docker images hosted on private registries,
simply add a :code:`setup` section to your task YAML file to authenticate with
the registry:

.. code-block:: yaml

  resources:
    accelerators: A100:1

  setup: |
    # Authenticate with private registry
    docker login -u <username> -p <password> <registry>

  run: |
    docker run <registry>/<image>:<tag>

Building containers remotely
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

If you are running containerized applications, the container image can also be built remotely on the cluster in the :code:`setup` phase of the task.

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
The output of the app produced at :code:`/outputs` path in the container is also volume mounted to :code:`/outputs` on the VM, which gets directly written to a S3 bucket through :ref:`bucket mounting <sky-storage>`.

Our GitHub repository has more examples, including running `Detectron2 in a Docker container <https://github.com/skypilot-org/skypilot/blob/master/examples/detectron2_docker.yaml>`_ via SkyPilot.

.. _docker-containers-as-runtime-environments:

Using Containers as Runtime Environments
----------------------------------------

When a container is used as the runtime environment, everything happens inside the container:

- The SkyPilot runtime is automatically installed and launched inside the container;
- :code:`setup` and :code:`run` commands are executed in the container;
- Any files created by the task will be stored inside the container.

To use a Docker image as your runtime environment, set the :code:`image_id` field in the :code:`resources` section of your task YAML file to :code:`docker:<image_id>`. Only **Debian-based** images (e.g., Ubuntu) are supported for now.

For example, to use the :code:`ubuntu:20.04` image from Docker Hub:

.. code-block:: yaml

  resources:
    image_id: docker:ubuntu:20.04

  setup: |
    # Commands to run inside the container

  run: |
    # Commands to run inside the container

As another example, here's how to use `NVIDIA's PyTorch NGC Container <https://catalog.ngc.nvidia.com/orgs/nvidia/containers/pytorch>`_:

.. code-block:: yaml

  resources:
    image_id: docker:nvcr.io/nvidia/pytorch:23.10-py3
    accelerators: T4

  setup: |
    # Commands to run inside the container

  run: |
    # Commands to run inside the container

    # Since SkyPilot tasks are run inside a fresh conda "(base)" environment,
    # deactivate first to access what the Docker image has already installed.
    source deactivate
    nvidia-smi
    python -c 'import torch; print(torch.__version__)'

Any GPUs assigned to the task will be automatically mapped to your Docker container, and all subsequent tasks within the cluster will also run inside the container. In a multi-node scenario, the container will be launched on all nodes, and the corresponding node's container will be assigned for task execution.

.. tip::

    **When to use this?**

    If you have a preconfigured development environment set up within a Docker
    image, it can be convenient to use the runtime environment mode.  This is
    especially useful for launching development environments that are
    challenging to configure on a new virtual machine, such as dependencies on
    specific versions of CUDA or cuDNN.

.. note::

    Since we ``pip install skypilot`` inside the user-specified container image
    as part of a launch, users should ensure dependency conflicts do not occur.

    Currently, the following requirements must be met:

    1. The container image should be based on Debian;

    2. The container image must grant sudo permissions without requiring password authentication for the user. Having a root user is also acceptable.

Private Registries
^^^^^^^^^^^^^^^^^^

When using this mode, to access Docker images hosted on private registries,
you can provide the registry authentication details using :ref:`task environment variables <env-vars>`:

.. tab-set::

    .. tab-item:: Docker Hub
        :sync: docker-hub-tab

        .. code-block:: yaml

          resources:
            image_id: docker:<user>/<your-docker-hub-repo>:<tag>

          envs:
            # Values used in: docker login -u <user> -p <password> <registry server>
            SKYPILOT_DOCKER_USERNAME: <user>
            SKYPILOT_DOCKER_PASSWORD: <password>
            SKYPILOT_DOCKER_SERVER: docker.io

    .. tab-item:: Cloud Provider Registry (e.g., ECR)
        :sync: csp-registry-tab

        .. code-block:: yaml

          resources:
            image_id: docker:<your-ecr-repo>:<tag>

          envs:
            # Values used in: docker login -u <user> -p <password> <registry server>
            SKYPILOT_DOCKER_USERNAME: AWS
            SKYPILOT_DOCKER_PASSWORD: <password>
            SKYPILOT_DOCKER_SERVER: <your-user-id>.dkr.ecr.<region>.amazonaws.com

We suggest setting the :code:`SKYPILOT_DOCKER_PASSWORD` environment variable through the CLI (see :ref:`passing secrets <passing-secrets>`):

.. code-block:: console

  $ # Docker Hub password:
  $ export SKYPILOT_DOCKER_PASSWORD=...
  $ # Or cloud registry password:
  $ export SKYPILOT_DOCKER_PASSWORD=$(aws ecr get-login-password --region us-east-1)
  $ # Pass --env:
  $ sky launch task.yaml --env SKYPILOT_DOCKER_PASSWORD
