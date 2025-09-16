.. _docker-containers:

Using Docker Containers
=======================

SkyPilot can run a container either as the runtime environment of compute nodes, or invoke it as a task.

* If the container image is to be used as a runtime environment (e.g., ``ubuntu``, ``nvcr.io/nvidia/pytorch:23.10-py3``, etc.) and if you have extra commands to run inside the container: run it :ref:`as a runtime environment <docker-containers-as-runtime-environments>`.
* If the container image is invocable / has an entrypoint: run it :ref:`as a task <docker-containers-as-tasks>`.

.. note::

    **RunPod specific:** Running docker containers is `not supported on RunPod <https://docs.runpod.io/references/faq#can-i-run-my-own-docker-daemon-on-runpod>`_. To use RunPod, either use your docker image :ref:`as a runtime environment <docker-containers-as-runtime-environments>` or use ``setup`` and ``run`` to configure your environment. See `GitHub issue <https://github.com/skypilot-org/skypilot/issues/3096#issuecomment-2150559797>`_ for more.



.. _docker-containers-as-runtime-environments:

Using containers as runtime environments
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

.. note::

  **RunPod specific:** For **non-root** docker images on RunPod, you must manually set the :code:`SKYPILOT_RUNPOD_DOCKER_USERNAME` environment variable to match the login user of the docker image (set by the last `USER` instruction in the Dockerfile).

  You can set this environment variable in the :code:`envs` section of your task YAML file:

  .. code-block:: yaml

    envs:
      SKYPILOT_RUNPOD_DOCKER_USERNAME: <ssh-user>

  It's a workaround for RunPod's limitation that we can't get the login user for the created pods, and even `runpodctl` uses a hardcoded `root` for SSH access.
  But for other clouds, the login users for the created docker containers are automatically fetched and used.

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

.. note::

  Using a container with a customized entrypoint as a runtime environment is
  supported, with the container's entrypoint being overridden by :code:`/bin/bash`.
  Specific commands can be executed in the :code:`setup` and :code:`run` sections
  of the task YAML file. However, this approach is not compatible with RunPod due
  to limitations in the RunPod API, so ensure that you choose a container with a
  default entrypoint (i.e. :code:`/bin/bash`).

.. _docker-containers-private-registries:

Private registries
^^^^^^^^^^^^^^^^^^

.. note::

    These instructions do not apply if you use SkyPilot to launch on Kubernetes clusters. Instead, see :ref:`Using Images from Private Repositories in Kubernetes<kubernetes-custom-images-private-repos>` for more.

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
            # The password should be a personal access token (PAT), see: https://app.docker.com/settings/personal-access-tokens
            SKYPILOT_DOCKER_USERNAME: <user>
            SKYPILOT_DOCKER_PASSWORD: <password>
            SKYPILOT_DOCKER_SERVER: docker.io

    .. tab-item:: AWS ECR
        :sync: aws-ecr-tab

        SkyPilot supports automatic AWS ECR authentication using command substitution. You can specify the authentication command directly in your YAML:

        .. code-block:: yaml

          resources:
            image_id: docker:<your-user-id>.dkr.ecr.<region>.amazonaws.com/<repo>:<tag>

          envs:
            # SkyPilot will execute the command and use its output as the password
            SKYPILOT_DOCKER_USERNAME: AWS
            SKYPILOT_DOCKER_PASSWORD: "$(aws ecr get-login-password --region <region>)"
            SKYPILOT_DOCKER_SERVER: <your-user-id>.dkr.ecr.<region>.amazonaws.com

        The AWS CLI command will be executed on your local machine when launching the cluster, and the resulting token will be used for authentication.

        Alternatively, you can use ``sky launch`` with the ``--env`` flag to pass the password:

        .. code-block:: bash

          sky launch sky.yaml \
            --env SKYPILOT_DOCKER_PASSWORD="$(aws ecr get-login-password --region us-east-1)"

    .. tab-item:: GCP GCR
        :sync: gcp-artifact-registry-tab

        We support private GCP Artifact Registry (GCR) with a service account key.
        See `GCP Artifact Registry authentication <https://cloud.google.com/artifact-registry/docs/docker/authentication?authuser=1#json-key>`_. Note that the ``SKYPILOT_DOCKER_USERNAME`` needs to be set to ``_json_key``.


        .. code-block:: yaml

          resources:
            image_id: docker:<your-gcp-project-id>/<your-registry-repository>/<your-image-name>:<tag>

          envs:
            SKYPILOT_DOCKER_USERNAME: _json_key
            SKYPILOT_DOCKER_PASSWORD: <gcp-service-account-key>
            SKYPILOT_DOCKER_SERVER: <location>-docker.pkg.dev

        Or, you can use ``sky launch`` with the ``--env`` flag to pass the service account key:

        .. code-block:: bash

          sky launch sky.yaml \
            --env SKYPILOT_DOCKER_PASSWORD="$(cat ~/gcp-key.json)"

        .. note::

            If your cluster is on GCP, SkyPilot will automatically use the IAM permissions of the instance to authenticate with GCR, if the ``SKYPILOT_DOCKER_USERNAME`` and ``SKYPILOT_DOCKER_PASSWORD`` are set to empty strings:

            .. code-block:: yaml

              envs:
                SKYPILOT_DOCKER_USERNAME: ""
                SKYPILOT_DOCKER_PASSWORD: ""
                SKYPILOT_DOCKER_SERVER: <location>-docker.pkg.dev

        .. note::

            ``RunPod`` requires Docker to authenticate to GAR using the `base64-encoded version of the key <https://contact.runpod.io/hc/en-us/articles/39403705226003-Help-to-setup-Google-Cloud-s-Artifact-Registry-GAR-with-RunPod>`_. To base64 encode the JSON key:

            .. code-block:: shell

              base64 -i gcp-key.json -w 0 > gcp-key.json.b64
            
            The Docker username should also be changed to ``_json_key_base64``:

            .. code-block:: yaml

              envs:
                SKYPILOT_DOCKER_USERNAME: _json_key_base64
                ...

            Furthermore, note that the base64 encoding option is only available on `Google Artifact Registry (GAR) <https://cloud.google.com/artifact-registry/docs>`_, not Google Container Registry (GCR), which has been `deprecated <https://cloud.google.com/artifact-registry/docs/transition/transition-from-gcr>`_ by Google.


    .. tab-item:: NVIDIA NGC
        :sync: nvidia-container-registry-tab

        .. code-block:: yaml

          resources:
            image_id: docker:nvidia/pytorch:23.10-py3

          envs:
            SKYPILOT_DOCKER_USERNAME: $oauthtoken
            SKYPILOT_DOCKER_PASSWORD: <NGC_API_KEY>
            SKYPILOT_DOCKER_SERVER: nvcr.io

        Or, you can use ``sky launch`` with the ``--env`` flag to pass the API key:

        .. code-block:: bash

          sky launch sky.yaml \
            --env SKYPILOT_DOCKER_PASSWORD=<NGC_API_KEY>

.. _docker-containers-as-tasks:

Running containers as tasks
---------------------------

.. note::

    On Kubernetes, running Docker runtime in a pod is not recommended. Instead, :ref:`use your container as a runtime environment <docker-containers-as-runtime-environments>`.

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

Private registries
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
