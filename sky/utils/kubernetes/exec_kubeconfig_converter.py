"""Helper script to strip path information from exec auth in a kubeconfig file.

This script processes a kubeconfig file and removes any path information from
the 'command' field in the exec configuration, leaving only the executable name.
This is useful when moving between different environments where auth plugin
executables might be installed in different locations.

It assumes the target environment has the auth executable available in PATH.
If not, you'll need to update your environment container to include the auth
executable in PATH.

Usage:
    python -m sky.utils.kubernetes.exec_kubeconfig_converter
"""
import argparse
import os

import yaml


def strip_auth_plugin_paths(kubeconfig_path: str, output_path: str):
    """Strip path information from exec plugin commands in a kubeconfig file.

    Args:
        kubeconfig_path (str): Path to the input kubeconfig file
        output_path (str): Path where the modified kubeconfig will be saved
    """
    with open(kubeconfig_path, 'r', encoding='utf-8') as file:
        config = yaml.safe_load(file)

    updated = False
    for user in config.get('users', []):
        exec_info = user.get('user', {}).get('exec', {})
        current_command = exec_info.get('command', '')

        if current_command:
            # Strip the path and keep only the executable name
            executable = os.path.basename(current_command)
            if executable != current_command:
                exec_info['command'] = executable
                updated = True

    if updated:
        with open(output_path, 'w', encoding='utf-8') as file:
            yaml.safe_dump(config, file)
        print('Kubeconfig updated with path-less exec auth. '
              f'Saved to {output_path}')
    else:
        print('No updates made. No exec-based auth commands paths found.')


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
    strip_auth_plugin_paths(args.input, args.output)


if __name__ == '__main__':
    main()
