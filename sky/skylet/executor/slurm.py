"""Slurm distributed task executor for SkyPilot.

This module is invoked on each Slurm compute node via:
    srun python -m sky.skylet.executor.slurm --script=... --log-dir=...
"""
import argparse
import json
import os
import pathlib
import socket
import subprocess
import sys
import time

import colorama

from sky.skylet.log_lib import run_bash_command_with_log


def _get_ip_address() -> str:
    """Get the IP address of the current node."""
    ip_result = subprocess.run(['hostname', '-I'],
                               capture_output=True,
                               text=True,
                               check=False)
    return ip_result.stdout.strip().split(
    )[0] if ip_result.returncode == 0 else 'unknown'


def _get_job_node_ips() -> str:
    """Get IPs of all nodes in the current Slurm job."""
    nodelist = os.environ.get('SLURM_JOB_NODELIST', '')
    assert nodelist, 'SLURM_JOB_NODELIST is not set'

    # Expand compressed nodelist (e.g., "node[1-3,5]"
    # -> "node1\nnode2\nnode3\nnode5")
    result = subprocess.run(['scontrol', 'show', 'hostnames', nodelist],
                            capture_output=True,
                            text=True,
                            check=False)
    if result.returncode != 0:
        raise RuntimeError(f'Failed to get hostnames for: {nodelist}')

    hostnames = result.stdout.strip().split('\n')
    ips = []
    for hostname in hostnames:
        try:
            ip = socket.gethostbyname(hostname)
            ips.append(ip)
        except socket.gaierror as e:
            raise RuntimeError('Failed to get IP for hostname: '
                               f'{hostname}') from e

    return '\n'.join(ips)


def main():
    parser = argparse.ArgumentParser(
        description='SkyPilot Slurm task runner for distributed execution')
    parser.add_argument('--script', help='User script (inline, shell-quoted)')
    parser.add_argument('--script-path',
                        help='Path to script file (if too long for inline)')
    parser.add_argument('--env-vars',
                        default='{}',
                        help='JSON-encoded environment variables')
    parser.add_argument('--log-dir',
                        required=True,
                        help='Directory for log files')
    parser.add_argument(
        '--is-setup',
        action='store_true',
        help=
        'Whether this is a setup command (affects logging prefix and filename)')
    parser.add_argument('--alloc-signal-file',
                        help='Path to allocation signal file')
    parser.add_argument('--setup-done-signal-file',
                        help='Path to setup-done signal file')
    args = parser.parse_args()

    assert args.script is not None or args.script_path is not None, (
        'Either '
        '--script or --script-path must be provided')

    # Compute per-node values from Slurm environment variables
    rank = int(os.environ['SLURM_PROCID'])
    num_nodes = int(
        os.environ.get('SLURM_NNODES', os.environ.get('SLURM_JOB_NUM_NODES',
                                                      1)))
    node_name = 'head' if rank == 0 else f'worker{rank}'
    if args.is_setup:
        # TODO(kevin): This is inconsistent with other clouds, where it is
        # simply called 'setup.log'. On Slurm that is obviously not possible,
        # since the ~/sky_logs directory is shared by all nodes, so
        # 'setup.log' will be overwritten by other nodes.
        # Perhaps we should apply this naming convention to other clouds.
        log_filename = f'setup-{node_name}.log'
    else:
        log_filename = f'{rank}-{node_name}.log'
    log_path = os.path.join(args.log_dir, log_filename)

    if args.script_path:
        with open(args.script_path, 'r', encoding='utf-8') as f:
            script = f.read()
    else:
        script = args.script

    # Parse env vars and add SKYPILOT environment variables
    env_vars = json.loads(args.env_vars)
    env_vars['SKYPILOT_NODE_RANK'] = str(rank)
    env_vars['SKYPILOT_NUM_NODES'] = str(num_nodes)
    env_vars['SKYPILOT_NODE_IPS'] = _get_job_node_ips()

    # Signal file coordination for setup/run synchronization
    # Rank 0 touches the allocation signal to indicate resources acquired
    if args.alloc_signal_file is not None and rank == 0:
        pathlib.Path(args.alloc_signal_file).touch()

    # Wait for setup to complete.
    while args.setup_done_signal_file is not None and not os.path.exists(
            args.setup_done_signal_file):
        time.sleep(0.1)

    # Build log prefix
    # For setup on head: (setup pid={pid})
    # For setup on workers: (setup pid={pid}, ip=1.2.3.4)
    # For run on head: (head, rank=0, pid={pid})
    # For run on workers: (worker1, rank=1, pid={pid}, ip=1.2.3.4)
    # The {pid} placeholder will be replaced by run_with_log
    if args.is_setup:
        # Setup prefix
        if rank == 0:
            prefix = (f'{colorama.Fore.CYAN}(setup pid={{pid}})'
                      f'{colorama.Style.RESET_ALL} ')
        else:
            ip = _get_ip_address()
            prefix = (f'{colorama.Fore.CYAN}(setup pid={{pid}}, ip={ip})'
                      f'{colorama.Style.RESET_ALL} ')
    else:
        # Run prefix with node name and rank
        if rank == 0:
            prefix = (
                f'{colorama.Fore.CYAN}({node_name}, rank={rank}, pid={{pid}})'
                f'{colorama.Style.RESET_ALL} ')
        else:
            ip = _get_ip_address()
            prefix = (f'{colorama.Fore.CYAN}'
                      f'({node_name}, rank={rank}, pid={{pid}}, ip={ip})'
                      f'{colorama.Style.RESET_ALL} ')

    returncode = run_bash_command_with_log(script,
                                           log_path,
                                           env_vars=env_vars,
                                           stream_logs=True,
                                           streaming_prefix=prefix)

    sys.exit(returncode)


if __name__ == '__main__':
    main()
