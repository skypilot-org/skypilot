"""
This script demonstrates the ability to use different backends with Sky.
It is useful to support a LocalBackend that users can use to debug their
programs even before they run them on the sky.
"""

import sky

# Set backend here. It can be either LocalDockerBackend or CloudVmRayBackend.
backend = sky.backends.LocalDockerBackend  # or sky.backends.CloudVmRayBackend

with sky.Dag() as dag:
    resources = sky.Resources(accelerators={'K80': 1})
    setup_commands = "apt-get update && apt-get install -y iputils-ping"
    task = sky.Task(run=f'ping 127.0.0.1 -c 100',
                    docker_image='ubuntu',
                    setup=setup_commands,
                    name="ping").set_resources(resources)

dag = sky.Optimizer.optimize(dag)
sky.execute(dag, backend=backend)