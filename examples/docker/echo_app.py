# Example using SkyPilot python API to run the echo app in a container.
#
# The echo example ingests a file, prints the contents and writes it back out.
# In this YAML, the output is mapped to a Sky Storage object, which writes to a
# cloud bucket.
#
# Usage:
#   python echo_app.py

import random
import string

import sky

with sky.Dag() as dag:
    # The setup command to build the container image
    setup = 'docker build -t echo:v0 /echo_app'

    # The command to run - runs the container and mounts volumes
    run = ('docker run --rm --volume="/inputs:/inputs:ro" '
           '--volume="/outputs:/outputs:rw" '
           'echo:v0 /inputs/README.md /outputs/output.txt')

    echo_app = sky.Task(
        setup=setup,
        run=run,
    )

    # Configure file mounts to copy local contents to remote
    echo_app.set_file_mounts({
        '/inputs': './echo_app',
        '/echo_app': './echo_app',
    })

    # Configure outputs for the task - we'll write to a bucket using Sky Storage
    output_bucket_name = ''.join(random.choices(string.ascii_lowercase, k=15))
    output_storage = sky.Storage(name=output_bucket_name,
                                 mode=sky.StorageMode.MOUNT)
    echo_app.set_storage_mounts({
        '/outputs': output_storage,
    })

    # Set resources if required
    # echo_app.set_resources({
    #     sky.Resources(accelerators='V100'),
    # })

sky.launch(dag)

print('Remember to clean up resources after this script is done!\n'
      'Run sky status and sky storage ls to list current resources.\n'
      'Run sky down <cluster_name> and sky storage delete <storage_name> to '
      'delete resources.')
