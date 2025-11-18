"""Use SkyPilot to launch a cluster or a service
to serve the vector database."""

#!/usr/bin/env python3

import argparse

import sky
from sky.serve.client import sdk as serve_sdk

_SERVICE_NAME = 'vectordb-serve'


def main():
    parser = argparse.ArgumentParser(
        description='Launch a cluster or a service to serve the vector database'
    )
    parser.add_argument(
        '--serve',
        action='store_true',
        help='Launch with sky serve. If false, launch a cluster.')
    args = parser.parse_args()

    # Load the task template
    task = sky.Task.from_yaml('serve_vectordb.yaml')
    resources = task.resources
    port = None
    for resource in resources:
        if resource.ports:
            ports = resource.ports
            if isinstance(ports, list):
                assert len(ports) == 1, 'Only one port is supported'
                port = ports[0]
            else:
                port = ports
            break

    if args.serve:
        req_id = serve_sdk.up(task, service_name=_SERVICE_NAME)
    else:
        req_id = sky.launch(task, cluster_name=_SERVICE_NAME)
    sky.stream_and_get(req_id)
    if args.serve:
        serve_status = sky.get(serve_sdk.status(service_names=_SERVICE_NAME))
        endpoint = serve_status[0]['endpoint']
    else:
        cluster_status = sky.get(sky.endpoints(cluster=_SERVICE_NAME))
        endpoint = cluster_status[int(port)]
    print(f'endpoint: {endpoint}')


if __name__ == '__main__':
    main()
