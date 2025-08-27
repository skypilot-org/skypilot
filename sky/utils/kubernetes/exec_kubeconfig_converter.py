"""Helper script to strip path information from exec auth in a kubeconfig file.

This script processes a kubeconfig file and removes any path information from
the 'command' field in the exec configuration, leaving only the executable name.
This is useful when moving between different environments where auth plugin
executables might be installed in different locations.

For Nebius kubeconfigs, it also changes the --profile argument to 'sky' to
ensure compatibility with SkyPilot's expected profile configuration.

It assumes the target environment has the auth executable available in PATH.
If not, you'll need to update your environment container to include the auth
executable in PATH.

When using LOCAL_CREDENTIALS (aka exec auth) with Kubernetes, though, SkyPilot
will automatically inject a wrapper script for common exec auth providers like
GKE and EKS. This wrapper script helps to resolve path issues that may arise
from executables installed on non system-default paths. Thus, the kubeconfig
file may look different on the sky jobs controller.

Usage:
    python -m sky.utils.kubernetes.exec_kubeconfig_converter
"""
import argparse
import os

import yaml

from sky.provision.kubernetes import utils as kubernetes_utils


def main():
    parser = argparse.ArgumentParser(
        description='Strip path information from exec plugin commands in a '
        'kubeconfig file. Used to prepare kubeconfigs for deployment '
        'with SkyPilot.')
    parser.add_argument(
        '--input',
        '-i',
        default=os.path.expanduser('~/.kube/config'),
        help='Input kubeconfig file path (default: %(default)s)')
    parser.add_argument(
        '--output',
        '-o',
        default=os.path.expanduser('~/.kube/config.converted'),
        help='Output kubeconfig file path (default: %(default)s)')

    args = parser.parse_args()

    with open(args.input, 'r', encoding='utf-8') as file:
        config = yaml.safe_load(file)

    updated = kubernetes_utils.format_kubeconfig_exec_auth(
        config, args.output, False)

    if updated:
        print('Kubeconfig updated with path-less exec auth. '
              f'Saved to {args.output}')
    else:
        print('No updates made.')


if __name__ == '__main__':
    main()
