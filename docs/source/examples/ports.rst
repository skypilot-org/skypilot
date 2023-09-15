.. _ports:

Opening Ports
=============

At times, it might be crucial to expose specific ports on your cluster to the public internet. For example:

- **Exposing Development Tools**: If you're working with tools like Jupyter Notebook or ray, you'll need to expose its port to access the interface / dashboard from your browser.
- **Creating Web Services**: Whether you're setting up a web server, database, or another service, they all communicate via specific ports that need to be accessible.
- **Collaborative Tools**: Some tools and platforms may require port openings to enable collaboration with teammates or to integrate with other services.

Opening Ports for SkyPilot cluster
----------------------------------

SkyPilot makes the process of opening ports simple. Here's a basic example in YAML configuration to expose a Jupyter Lab server:

.. code-block:: yaml

    # jupyter_lab.yaml
    resources:
      ports: 8888

    setup: pip install jupyter

    run: jupyter lab --port 8888 --no-browser --ip=0.0.0.0

In this example, by specifying :code:`ports` in the :code:`resources` section, SkyPilot will automatically open port 8888 on your cluster. The :code:`run` command will start the Jupyter Lab server on port 8888. To access the server, run:

.. code-block:: bash

    $ sky launch -c jupyter jupyter_lab.yaml

and look for the logs fo some output like:

.. code-block:: bash

    Jupyter Server 2.7.0 is running at:
        http://127.0.0.1:8888/lab?token=<token>

Run

.. code-block:: bash

    $ sky status -a jupyter

to get the :code:`HEAD_IP` of the cluster, replace the :code:`127.0.0.1` with the :code:`HEAD_IP` and open your browser for the URL.

If you want to expose multiple ports, you can specify a list of ports or port ranges in the :code:`resources` section:

.. code-block:: yaml

    resources:
      ports:
        - 8888
        - 10020-10040
        - 20000-20010

SkyPilot also support opening ports through the CLI:

.. code-block:: bash

    $ sky launch -c jupyter --ports 8888 jupyter_lab.yaml

Things You Should Know
----------------------

Before you start opening ports, there are a few things you need to bear in mind:

- **Public Accessibility**: Ports you open are exposed to the public internet. It means anyone who knows your VM's IP address and the opened port can try to access your service. Ensure you use security measures, like authentication mechanisms, to protect your services.
- **Lifecycle Management**: SkyPilot retains the state of all opened ports, even after individual tasks have finished. The rationale behind this is the uncertainty regarding whether tasks, once completed, left behind some backend programs that are still using these ports. Such situations are common when commands like :code:`docker run` or :code:`cmd &` are used, leaving tasks running in the background. To avoid potential disruption or connectivity issues, all previously opened ports remain accessible. The only instance when ports are automatically cleaned up is during cluster shutdown. At this moment, all ports that were opened during the cluster's active lifecycle are conclusively closed. Simultaneously, all corresponding firewall rules and security groups associated with these ports are cleaned up.
- **Network Costs**: Data transfer costs might be associated with cloud providers when data is sent in and out of your VM. Be aware of the potential costs, especially if you expect high traffic on your opened ports.
