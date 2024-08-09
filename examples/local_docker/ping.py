"""An example app which pings localhost.

This script is designed to demonstrate the use of different backends with
SkyPilot.  It is useful to support a LocalDockerBackend that users can use to
debug their programs even before they run them on the Sky.
"""

import apex

# Set backend here. It can be either LocalDockerBackend or CloudVmRayBackend.
backend = apex.backends.LocalDockerBackend(
)  # or sky.backends.CloudVmRayBackend()

with apex.Dag() as dag:
    resources = apex.Resources(accelerators={'K80': 1})
    setup_commands = 'apt-get update && apt-get install -y iputils-ping'
    task = apex.Task(run='ping 127.0.0.1 -c 100',
                    docker_image='ubuntu',
                    setup=setup_commands,
                    name='ping').set_resources(resources)

apex.launch(dag, backend=backend)
