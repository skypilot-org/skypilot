.. _ports:

Opening Ports
=============

At times, it might be crucial to expose specific ports on your cluster to the public internet. For example:

- **Exposing Development Tools**: If you're working with tools like Jupyter Notebook or ray, you'll need to expose its port to access the interface / dashboard from your browser.
- **Creating Web Services**: Whether you're setting up a web server, database, or another service, they all communicate via specific ports that need to be accessible.
- **Collaborative Tools**: Some tools and platforms may require port openings to enable collaboration with teammates or to integrate with other services.

Opening ports on a cluster
----------------------------------

To open a port on a SkyPilot cluster, specify :code:`ports` in the :code:`resources` section of your task. For example, here is a YAML configuration to expose a Jupyter Lab server:

.. code-block:: yaml

    # jupyter_lab.yaml
    resources:
      ports: 8888

    setup: pip install jupyter

    run: jupyter lab --port 8888 --no-browser --ip=0.0.0.0

In this example, the :code:`run` command will start the Jupyter Lab server on port 8888. By specifying :code:`ports: 8888`, SkyPilot will expose port 8888 on the cluster, making the jupyter server publicly accessible. To launch and access the server, run:

.. code-block:: console

    $ sky launch -c jupyter jupyter_lab.yaml

and look in for the logs for some output like:

.. code-block:: console

    Jupyter Server 2.7.0 is running at:
        http://127.0.0.1:8888/lab?token=<token>


To get the endpoint URL for the exposed port, run :code:`sky status --endpoint 8888 jupyter`:

.. code-block:: console

    $ sky status --endpoint 8888 jupyter
    http://35.223.97.21:8888

You can then directly open this URL in your browser, replacing the token with the one from the jupyter server logs.

Alternatively, you can view all exposed endpoints at once using :code:`sky status --endpoints jupyter`:

.. code-block:: console

    $ sky status --endpoints jupyter
    8888: http://35.223.97.21:8888

If you want to expose multiple ports, you can specify a list of ports or port ranges in the :code:`resources` section:

.. code-block:: yaml

    resources:
      ports:
        - 8888
        - 10020-10040
        - 20000-20010

SkyPilot also supports opening ports through the CLI:

.. code-block:: console

    $ sky launch -c jupyter --ports 8888 jupyter_lab.yaml

Security and lifecycle considerations
-------------------------------------

Before you start opening ports, there are a few things you need to bear in mind:

- **Public Accessibility**: Ports you open are exposed to the public internet. It means anyone who knows your VM's IP address and the opened port can access your service. Ensure you use security measures, like authentication mechanisms, to protect your services.
- **Lifecycle Management**: All opened ports are kept open, even after individual tasks have finished. The only instance when ports are automatically closed is during cluster shutdown. At shutdown, all ports that were opened during the cluster's lifespan are closed. Simultaneously, all corresponding firewall rules and security groups associated with these ports are also cleaned up.
