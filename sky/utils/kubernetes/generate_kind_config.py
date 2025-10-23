"""Generates a kind cluster config file

Maps specified ports from host to cluster container.
"""
import argparse

from sky.utils.kubernetes import kubernetes_deploy_utils

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Generate a kind cluster '
                                     'config file with ports mapped'
                                     ' from host to container')
    parser.add_argument('--path',
                        type=str,
                        default='/tmp/skypilot-kind.yaml',
                        help='Path to generate the config file at')
    parser.add_argument('--port-start',
                        type=int,
                        default=30000,
                        help='Port range start')
    parser.add_argument('--num-nodes',
                        type=int,
                        default=1,
                        help='Number of nodes in the cluster')
    # Add GPU support
    parser.add_argument('--gpus',
                        action='store_true',
                        help='Initialize kind cluster with GPU support')
    args = parser.parse_args()

    with open(args.path, 'w', encoding='utf-8') as f:
        f.write(
            kubernetes_deploy_utils.generate_kind_config(
                args.port_start, args.num_nodes, args.gpus))
