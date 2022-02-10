Python Control Flow
===================

For advanced use cases, we allow users to automate workflows on Sky with our
Python API.

Below is a simple task that can be expressed as a Python script:

.. code-block:: python

   # hello_sky.py

   import sky

   backend = sky.backends.CloudVmRayBackend()

   with sky.Dag() as dag:
      resources = sky.Resources()
      setup_commands = 'echo "Hello, Sky!"'
      task = sky.Task(run='ping 127.0.0.1 -c 5',
                     setup=setup_commands,
                     name='ping').set_resources(resources)

   sky.launch(dag, cluster_name="mycluster", backend=backend, stream_logs=True)

The task can be launched by running the Python script:

.. code-block:: console

   $ python hello_sky.py

The above is equivalent to running :code:`sky launch -c mycluster` with the following YAML spec:

.. code-block:: yaml

   name: ping
   setup: echo "Hello, Sky!"
   run: ping 127.0.0.1 -c 5

Please see our full API documentation for further details.
