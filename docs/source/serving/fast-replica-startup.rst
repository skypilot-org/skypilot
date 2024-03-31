Speeding Up Replica Setup
=========================

When serving AI models, the setup process like dependencies installation and model weights downloading may take a lot of time. To speed up this process, you can use the following methods:

Use a Custom Image for VMs
--------------------------

You can use a custom image that has the dependencies preinstalled and, optionally, model weights downloaded. This can be done by creating a VM, installing the dependencies, and then creating a custom image from the VM on the cloud console. Then, you can use the custom image to launch the VMs for serving:

.. code-block:: yaml
  :emphasize-lines: 8-9

    service:
      replicas: 2
      readiness_probe: /v1/models

    resources:
      ports: 8080
      accelerators: A100:1
      cloud: gcp
      image_id: projects/my-project/global/machineImages/image-with-dependency-installed

    # Followed by setup and run commands.

Notice that the :code:`cloud` field must be specified when :code:`image_id` is used. 

To support launching on multiple clouds, use :code:`any_of` in :code:`resources` :

.. code-block:: yaml
  :emphasize-lines: 8-14

    service:
      replicas: 2
      readiness_probe: /v1/models

    resources:
      ports: 8080
      accelerators: A100:1
      any_of:
        - cloud: gcp
          image_id: projects/my-project/global/machineImages/image-with-dependency-installed
        - cloud: aws
          image_id:
            us-east-1: ami-0729d913a335efca7
            us-west-2: ami-050814f384259894c

    # Followed by setup and run commands.

Notice that AWS needs a per-region image since an AMI image is associated with a particular region. While this requires creating several images on the cloud console, it can significantly speed up the dependency installation. This approach also takes advantage of the in-region fast network, which delivers a quicker download speed of the machine image.

Use Docker Containers as Runtime Environment
--------------------------------------------

You can also :ref:`use docker containers as runtime environment <docker-containers-as-runtime-environments>`. This can be done by creating a Docker image that has the dependencies preinstalled and (optionally) model weights downloaded, and then using the container image to launch the VMs for serving:

.. code-block:: yaml
  :emphasize-lines: 8

    service:
      replicas: 2
      readiness_probe: /v1/models

    resources:
      ports: 8080
      accelerators: A100:1
      image_id: docker:docker-image-with-dependency-installed

    # Followed by setup and run commands.

Your docker image can have all skypilot dependencies pre-installed to further reduce the setup time. You could try building your docker image based from our base image. The :code:`Dockerfile` could look like the following:

.. code-block:: Dockerfile

    FROM docker:berkeleyskypilot/skypilot-k8s-gpu:latest

    # Your dependencies installation and model download code goes here.
    # An example to install dependencies for vLLM:
    RUN conda create -n vllm python=3.10 -y
    SHELL ["conda", "run", "-n", "vllm", "/bin/bash", "-c"]
    RUN pip install vllm==0.3.0 transformers==4.37.2

This is easier to configure than machine images, but it may have a longer startup time than machine images since it needs to pull the docker image from the registry in addition to the usual VM setup.