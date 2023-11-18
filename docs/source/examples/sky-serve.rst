.. _sky-serve:

Sky Serve
=========

Sky Serve is SkyPilot's serving library. Sky Serve takes an existing serving
framework and deploys it across one or more regions or clouds.

.. * Serve on scarce resources (e.g., A100; spot) with **reduced costs and increased availability**

Why Sky Serve?

* **Bring any serving framework** (vLLM, TGI, FastAPI, ...) and scale it across regions/clouds
* **Reduce costs and increase availability** of service replicas by leveraging multiple/cheaper locations and hardware
* **Out-of-the-box load-balancing and autoscaling** of service replicas
* Manage multi-cloud, multi-region deployments with a single control plane
* Everything is launched inside your cloud accounts and VPCs

.. * Allocate scarce resources (e.g., A100) **across regions and clouds**
.. * Autoscale your endpoint deployment with load balancing
.. * Manage your multi-cloud resources with a single control plane

How it works

- Each service gets an endpoint that automatically redirects requests to its underlying replicas.
- The replicas of the same service can run in different regions and clouds — reducing cloud costs and increasing availability.
- Sky Serve transparently handles the load balancing, failover, and autoscaling of the serving replicas.

.. GPU availability has become a critical bottleneck for many AI services. With Sky
.. Serve, we offer a lightweight control plane that simplifies deployment across
.. many cloud providers. By consolidating availability and pricing data across
.. clouds, we ensure **timely execution at optimal costs**, addressing the
.. complexities of managing resources in a multi-cloud environment.


Sky Serve provides a simple CLI interface to deploy and manage your services. It
features a simple YAML spec to describe your services (referred to as a *service
YAML* in the following) and a centralized controller to manage the deployments.

Hello, Sky Serve!
-----------------

Here we will go through an example to deploy a simple HTTP server with Sky Serve. To spin up a service, you can simply reuse your task YAML with the two following requirements:

#. An HTTP endpoint and the port on which it listens;
#. An extra :code:`service` section in your task YAML to describe the service configuration.

It is recommended to test it with :code:`sky launch` first. For example, we have the following task YAML works with :code:`sky launch`:

.. code-block:: yaml

    resources:
      ports: 8080
      cpus: 2

    workdir: .

    run: python -m http.server 8080

And under the same directory, we have an :code:`index.html`:

.. code-block:: html

    <html>
    <head>
        <title>My First Sky Serve Service</title>
    </head>
    <body>
        <p>Hello, Sky Serve!</p>
    </body>
    </html>


.. note::

  :ref:`workdir <sync-code-artifacts>` and :ref:`file mounts with local files <sync-code-artifacts>` will be automatically uploaded to
  :ref:`SkyPilot Storage <sky-storage>`. Cloud bucket will be created, and cleaned up after the service is terminated.

Notice that task YAML already have a running HTTP endpoint at 8080, and exposed through the :code:`ports` section under :code:`resources`. Suppose we want to scale it into multiple replicas across multiple regions/clouds with Sky Serve. We can simply add a :code:`service` section to the YAML:

.. code-block:: yaml

    # hello-sky-serve.yaml
    service:
      readiness_probe: /health
      replicas: 2

    resources:
      ports: 8080
      cpus: 2

    workdir: .

    run: python -m http.server 8080

You can found more configurations in :ref:`here <service-yaml-spec>`. This example will spin up two replicas of the service, each listening on port 8080. The service is considered ready when it responds to :code:`GET /health` with a 200 status code. You can customize the readiness probe by specifying a different path in the :code:`readiness_probe` field. By calling:

.. code-block:: console

    $ sky serve up hello-sky-serve.yaml

Sky Serve will start a centralized controller/load balancer and deploy the service to the cloud with the best price/performance ratio. It will also monitor the service status and re-launch a new replica if one of them fails.

Under the hood, :code:`sky serve up`:

#. Launches a controller which handles autoscaling, monitoring and load balancing;
#. Returns an Service Endpoint which will be used to accept traffic;
#. Meanwhile, the controller provisions replica VMs which later run the services;
#. Once any replica is ready, the requests sent to the Service Endpoint will be **HTTP-redirect** to one of the endpoint replicas.

After the controller is provisioned, you'll see:

.. code-block:: console

    Service name: sky-service-e4fb
    Endpoint URL: <endpoint-url>
    To see detailed info:           sky serve status sky-service-e4fb [--endpoint]
    To teardown the service:        sky serve down sky-service-e4fb

    To see logs of a replica:       sky serve logs sky-service-e4fb [REPLICA_ID]
    To see logs of load balancer:   sky serve logs --load-balancer sky-service-e4fb
    To see logs of controller:      sky serve logs --controller sky-service-e4fb

    To monitor replica status:      watch -n10 sky serve status sky-service-e4fb
    To send a test request:         curl -L <endpoint-url>

    SkyServe is spinning up your service now.
    The replicas should be ready within a short time.

Once any of the replicas becomes ready to serve, you can start sending requests to :code:`<endpoint-url>`. You can use :code:`watch -n10 sky serve status sky-service-e4fb` to monitor the latest status of the service. Once its status becomes :code:`READY`, you can start sending requests to :code:`<endpoint-url>`:

.. code-block:: console

    $ curl -L <endpoint-url>
    <html>
    <head>
        <title>My First Sky Serve Service</title>
    </head>
    <body>
        <p>Hello, Sky Serve!</p>
    </body>
    </html>

.. note::

  The :code:`curl` command won't follow the redirect and print the content of the redirected page by default. Since we are using HTTP-redirect, you need to use :code:`curl -L <endpoint-url>`.

Sky Serve Architecture
----------------------

.. image:: ../images/sky-serve-architecture.png
    :width: 600
    :align: center
    :alt: Sky Serve Architecture

Sky Serve has a centralized controller VM that manages the deployment of your service. Each service will have a process group to manage its replicas and route traffic to them.

It is composed of the following components:

#. **Controller**: The controller will monitor the status of the replicas and re-launch a new replica if one of them fails. It also autoscales the number of replicas if autoscaling config is set (see :ref:`Service YAML spec <service-yaml-spec>` for more information).
#. **Load Balancer**: The load balancer will route the traffic to all ready replicas. It is a lightweight HTTP server that listens on the service endpoint and **HTTP-redirects** the requests to one of the replicas.

All of the process group shares a single controller VM. The controller VM will be launched in the cloud with the best price/performance ratio. You can also :ref:`customize the controller resources <customizing-sky-serve-controller-resources>` based on your needs.

An end-to-end LLM example
-------------------------

Below we show an end-to-end example of deploying a LLM model with Sky Serve. We'll use the `Vicuna OpenAI API Endpoint YAML <https://github.com/skypilot-org/skypilot/blob/master/llm/vicuna/serve-openai-api-endpoint.yaml>`_ as an example:

.. code-block:: yaml

    resources:
      ports: 8080
      accelerators: A100:1
      disk_size: 1024
      disk_tier: high

    setup: |
      conda activate chatbot
      if [ $? -ne 0 ]; then
        conda create -n chatbot python=3.9 -y
        conda activate chatbot
      fi

      # Install dependencies
      pip install "fschat[model_worker,webui]==0.2.24"
      pip install protobuf

    run: |
      conda activate chatbot

      echo 'Starting controller...'
      python -u -m fastchat.serve.controller > ~/controller.log 2>&1 &
      sleep 10
      echo 'Starting model worker...'
      python -u -m fastchat.serve.model_worker \
                --model-path lmsys/vicuna-${MODEL_SIZE}b-v1.3 2>&1 \
                | tee model_worker.log &

      echo 'Waiting for model worker to start...'
      while ! `cat model_worker.log | grep -q 'Uvicorn running on'`; do sleep 1; done

      echo 'Starting openai api server...'
      python -u -m fastchat.serve.openai_api_server --host 0.0.0.0 --port 8080 | tee ~/openai_api_server.log

    envs:
      MODEL_SIZE: 13

The above SkyPilot Task YAML will launch an OpenAI API endpoint with a 13B Vicuna model. This YAML can be used with :code:`sky launch` to launch a single replica of the service. By adding a :code:`service` section to the YAML, we can scale it into multiple replicas across multiple regions/clouds:

.. code-block:: yaml

    # vicuna.yaml
    service:
      readiness_probe: /v1/models
      replicas: 2

    resources:
      ports: 8080
      # Here goes other resources config

    # Here goes other task config

Now you have an Service YAML that can be used with Sky Serve! Simply run :code:`sky serve up vicuna.yaml -n vicuna` to deploy the service (use :code:`-n` to give your service a name!). After a while, you'll see:

.. code-block:: console

    Service name: vicuna
    Endpoint URL: <vicuna-url>
    To see detailed info:           sky serve status vicuna [--endpoint]
    To teardown the service:        sky serve down vicuna

    To see logs of a replica:       sky serve logs vicuna [REPLICA_ID]
    To see logs of load balancer:   sky serve logs --load-balancer vicuna
    To see logs of controller:      sky serve logs --controller vicuna

    To monitor replica status:      watch -n10 sky serve status vicuna
    To send a test request:         curl -L <vicuna-url>

After a while, there will be an OpenAI Compatible API endpoint ready to serve at :code:`<vicuna-url>`. Try out by the following simple chatbot Python script:

.. code-block:: python

    import openai

    stream = True
    model = 'vicuna-13b-v1.3' # This is aligned with the MODEL_SIZE env in the YAML
    init_prompt = 'You are a helpful assistant.'
    history = [{'role': 'system', 'content': init_prompt}]
    endpoint = input('Endpoint: ')
    openai.api_base = f'http://{endpoint}/v1'
    openai.api_key = 'placeholder'

    try:
        while True:
            user_input = input('[User] ')
            history.append({'role': 'user', 'content': user_input})
            resp = openai.ChatCompletion.create(model=model,
                                                messages=history,
                                                stream=True)
            print('[Chatbot]', end='', flush=True)
            tot = ''
            for i in resp:
                dlt = i['choices'][0]['delta']
                if 'content' not in dlt:
                    continue
                print(dlt['content'], end='', flush=True)
                tot += dlt['content']
            print()
            history.append({'role': 'assistant', 'content': tot})
    except KeyboardInterrupt:
        print('\nBye!')

Useful CLIs
-----------

Here are some commands for sky serve. Check :code:`sky serve --help` for more details.

See all running services:

.. code-block:: console

    $ sky serve status

.. code-block:: console

    Services
    NAME         UPTIME      STATUS  REPLICAS  ENDPOINT
    llama2-spot  2h 29m 36s  READY   1/2       34.238.42.4:30001
    vicuna       3h 5m 56s   READY   2/2       34.238.42.4:30003
    http-server  3h 20m 50s  READY   2/2       34.238.42.4:30002

    Service Replicas
    SERVICE_NAME  ID  IP              LAUNCHED   RESOURCES                   STATUS  REGION
    llama2-spot   1   34.90.186.40    2 hrs ago  1x GCP([Spot]{'A100': 1}))  READY   europe-west4
    llama2-spot   2   34.147.124.113  2 hrs ago  1x GCP([Spot]{'A100': 1}))  READY   europe-west4
    vicuna        1   35.247.122.252  3 hrs ago  1x GCP({'A100': 1}))        READY   us-west1
    vicuna        2   34.141.221.32   3 hrs ago  1x GCP({'A100': 1}))        READY   europe-west4
    http-server   1   3.95.5.141      3 hrs ago  1x AWS(vCPU=2)              READY   us-east-1
    http-server   2   54.175.170.174  3 hrs ago  1x AWS(vCPU=2)              READY   us-east-1

Stream the logs of a service:

.. code-block:: console

    $ sky serve logs vicuna --controller # tail controller logs
    $ sky serve logs vicuna --load-balancer --no-follow # print the load balancer logs so far, and exit
    $ sky serve logs vicuna 2 # tail logs of replica 2, including provisioning and running logs

Terminate services:

.. code-block:: console

    $ sky serve down http-server # terminate the http-server service
    $ sky serve down --all # terminate all services

Sky Serve controller
--------------------

The sky serve controller is a small on-demand CPU VM running in the cloud that:

#. Manages the deployment of your service;
#. Monitors the status of your service;
#. Routes traffic to your service replicas.

It is automatically launched when the first service is deployed, and it is autostopped after it has been idle for 10 minutes (i.e., after all services are terminated).
Thus, **no user action is needed** to manage its lifecycle.

You can see the controller with :code:`sky status` and refresh its status by using the :code:`-r/--refresh` flag.

.. _customizing-sky-serve-controller-resources:

Customizing sky serve controller resources
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

You may want to customize the resources of the sky serve controller for several reasons:

1. Use a lower-cost controller. (if you have a few services running)
2. Enforcing the controller to run on a specific location. This is particularly useful when you want the service endpoint within specific geographical region. (Default: cheapest location)
3. Changing the maximum number of services that can be run concurrently, which is the minimum number between 4x the vCPUs of the controller and the memory in GiB of the controller. (Default: 16)
4. Changing the disk_size of the controller to store more logs. (Default: 200GB)

To achieve the above, you can specify custom configs in :code:`~/.sky/config.yaml` with the following fields:

.. code-block:: yaml

  serve:
    # NOTE: these settings only take effect for a new sky serve controller, not if
    # you have an existing one.
    controller:
      resources:
        # All configs below are optional.
        # Specify the location of the sky serve controller.
        cloud: gcp
        region: us-central1
        # Specify the maximum number of services that can be run concurrently.
        cpus: 2+  # number of vCPUs, max concurrent services = min(4 * cpus, memory in GiB)
        # Specify the disk_size in GB of the sky serve controller.
        disk_size: 1024

The :code:`resources` field has the same spec as a normal SkyPilot job; see `here <https://skypilot.readthedocs.io/en/latest/reference/yaml-spec.html>`__.

.. note::
  These settings will not take effect if you have an existing controller (either
  stopped or live).  For them to take effect, tear down the existing controller
  first, which requires all services to be terminated.
