"""Read a label from this pod's Kubernetes Node."""

import argparse
import json
import os
import ssl
import urllib.parse
import urllib.request


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--node', required=True)
    parser.add_argument('--label', required=True)
    args = parser.parse_args()

    service_host = os.environ['KUBERNETES_SERVICE_HOST']
    service_port = os.environ.get('KUBERNETES_SERVICE_PORT_HTTPS', '443')
    service_account_root = '/var/run/secrets/kubernetes.io/serviceaccount'
    with open(f'{service_account_root}/token', encoding='utf-8') as token_file:
        token = token_file.read().strip()

    node_name = urllib.parse.quote(args.node, safe='')
    url = f'https://{service_host}:{service_port}/api/v1/nodes/{node_name}'
    request = urllib.request.Request(
        url,
        headers={
            'Accept': 'application/json',
            'Authorization': f'Bearer {token}',
        },
    )
    context = ssl.create_default_context(
        cafile=f'{service_account_root}/ca.crt')
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=context),
    )
    with opener.open(request, timeout=30) as response:
        node = json.load(response)

    labels = node.get('metadata', {}).get('labels', {})
    value = labels.get(args.label)
    if not value:
        raise RuntimeError(f'Kubernetes Node {args.node!r} has no non-empty '
                           f'{args.label!r} label')
    print(value)


if __name__ == '__main__':
    main()
