"""Generates a kind cluster config file

Maps specified ports from host to cluster container.
"""
import argparse
import textwrap


def generate_kind_config(path: str,
                         port_start: int = 30000,
                         port_end: int = 32768,
                         num_nodes: int = 1,
                         gpus: bool = False) -> None:
    """Generate a kind cluster config with ports mapped from host to container

    Args:
        path: Path to generate the config file at
        port_start: Port range start
        port_end: Port range end
        num_nodes: Number of nodes in the cluster
        gpus: If true, initialize kind cluster with GPU support
    """

    preamble = textwrap.dedent(f"""
    apiVersion: kind.x-k8s.io/v1alpha4
    kind: Cluster
    kubeadmConfigPatches:
    - |
      kind: ClusterConfiguration
      apiServer:
        extraArgs:
          "service-node-port-range": {port_start}-{port_end}
    nodes:
    - role: control-plane
      kubeadmConfigPatches:
      - |
        kind: InitConfiguration
        nodeRegistration:
          kubeletExtraArgs:
            node-labels: "ingress-ready=true"
    """)
    if gpus:
        preamble += textwrap.indent(
            textwrap.dedent("""
        extraMounts:
        - hostPath: /dev/null
          containerPath: /var/run/nvidia-container-devices/all"""), ' ' * 2)
    preamble += textwrap.indent(
        textwrap.dedent("""
      extraPortMappings:"""), ' ' * 2)
    suffix = ''
    if num_nodes > 1:
        for _ in range(1, num_nodes):
            suffix += """- role: worker\n"""
    with open(path, 'w', encoding='utf-8') as f:
        f.write(preamble)
        for port in range(port_start, port_end + 1):
            f.write(f"""
      - containerPort: {port}
        hostPort: {port}
        listenAddress: "0.0.0.0"
        protocol: tcp""")
        f.write('\n')
        if suffix:
            f.write(suffix)


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
    parser.add_argument('--port-end',
                        type=int,
                        default=32768,
                        help='Port range end')
    parser.add_argument('--num-nodes',
                        type=int,
                        default=1,
                        help='Number of nodes in the cluster')
    # Add GPU support
    parser.add_argument('--gpus',
                        action='store_true',
                        help='Initialize kind cluster with GPU support')
    args = parser.parse_args()
    generate_kind_config(args.path, args.port_start, args.port_end,
                         args.num_nodes, args.gpus)
