#!/usr/bin/env python3
"""SkyServe v2 CLI - standalone entry point for testing.

Usage:
    python -m sky.serve.skyserve_v2_cli up <yaml_path> [--namespace NS] [--direct] [--context CTX]
    python -m sky.serve.skyserve_v2_cli status [SERVICE_NAME] [--namespace NS] [--context CTX]
    python -m sky.serve.skyserve_v2_cli down SERVICE_NAME [--namespace NS] [--context CTX]
    python -m sky.serve.skyserve_v2_cli logs SERVICE_NAME [--namespace NS] [--tail N] [--context CTX]
    python -m sky.serve.skyserve_v2_cli endpoint SERVICE_NAME [--namespace NS] [--context CTX]
    python -m sky.serve.skyserve_v2_cli prereqs [--context CTX]
    python -m sky.serve.skyserve_v2_cli install [--context CTX]
    python -m sky.serve.skyserve_v2_cli generate <yaml_path> [--mode kserve|direct]
"""
import argparse
import json
import logging
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from sky.serve import kserve_generator
from sky.serve import kserve_prereqs
from sky.serve import model_registry
from sky.serve import serve_spec_v2
from sky.serve import serve_v2


def cmd_up(args):
    """Deploy a model service."""
    result = serve_v2.up(
        yaml_path=args.yaml_path,
        namespace=args.namespace,
        hf_token=args.hf_token,
        force_direct=args.direct,
        context=args.context,
        auto_install=not args.no_auto_install,
        service_type=args.service_type,
    )
    print(f'\nService deployed: {result["service_name"]}')
    if result.get('endpoint'):
        print(f'Endpoint: {result["endpoint"]}')
    print(f'To check status: python -m sky.serve.skyserve_v2_cli status '
          f'{result["service_name"]} --namespace {args.namespace}')


def cmd_status(args):
    """Check service status."""
    serve_v2.print_status(
        service_name=args.service_name,
        namespace=args.namespace,
        context=args.context,
    )


def cmd_down(args):
    """Tear down a service."""
    serve_v2.down(
        service_name=args.service_name,
        namespace=args.namespace,
        context=args.context,
    )


def cmd_logs(args):
    """View service logs."""
    output = serve_v2.logs(
        service_name=args.service_name,
        namespace=args.namespace,
        tail=args.tail,
        replica=args.replica,
        context=args.context,
    )
    print(output)


def cmd_endpoint(args):
    """Get service endpoint."""
    url = serve_v2.endpoint(
        service_name=args.service_name,
        namespace=args.namespace,
        context=args.context,
    )
    if url:
        print(f'Endpoint: {url}')
        print(f'\nTo access locally:')
        ctx_flag = f' --context {args.context}' if args.context else ''
        print(f'  kubectl port-forward svc/{args.service_name} '
              f'8000:8000 -n {args.namespace}{ctx_flag}')
        print(f'  curl http://localhost:8000/v1/models')
    else:
        print('No endpoint found (service may still be starting)')


def cmd_prereqs(args):
    """Check prerequisites."""
    checks = kserve_prereqs.check_all(context=args.context)
    kserve_prereqs.print_prereq_report(checks)


def cmd_install(args):
    """Auto-install all prerequisites."""
    from sky.serve import kserve_installer
    print('Installing SkyServe v2 prerequisites...')
    results = kserve_installer.ensure_all_prerequisites(context=args.context)

    print('\n\nInstallation Summary:')
    print('=' * 50)
    for name, status in results.items():
        print(f'  {status}')

    all_ok = all(s.installed for s in results.values())
    if all_ok:
        print('\nAll prerequisites installed successfully.')
    else:
        failed = [n for n, s in results.items() if not s.installed]
        print(f'\nSome components failed: {", ".join(failed)}')


def cmd_generate(args):
    """Generate K8s resources without applying."""
    spec = serve_spec_v2.parse_spec(args.yaml_path)
    errors = serve_spec_v2.validate_spec(spec)
    if errors:
        print(f'Errors: {"; ".join(errors)}')
        sys.exit(1)

    hf_token = args.hf_token or os.environ.get('HF_TOKEN')

    if args.mode == 'kserve':
        resources, model_config = kserve_generator.generate_resources(
            spec, namespace=args.namespace, hf_token=hf_token)
    else:
        resources, model_config = kserve_generator.generate_direct_deployment(
            spec, namespace=args.namespace, hf_token=hf_token)

    print(f'# Generated for: {model_config.model_id}')
    print(f'# GPU: {model_config.num_gpus}x {model_config.gpu_type}')
    print(f'# TP: {model_config.tensor_parallel}')
    print(f'# Mode: {args.mode}')
    print('---')
    print(kserve_generator.resources_to_yaml(resources))


def main():
    parser = argparse.ArgumentParser(
        description='SkyServe v2 CLI')
    parser.add_argument('-v', '--verbose', action='store_true',
                       help='Verbose logging')
    subparsers = parser.add_subparsers(dest='command', required=True)

    # up
    p_up = subparsers.add_parser('up', help='Deploy a model service')
    p_up.add_argument('yaml_path', help='Path to SkyServe v2 YAML spec')
    p_up.add_argument('--namespace', default='skyserve-v2')
    p_up.add_argument('--hf-token', default=None,
                     help='HuggingFace token (or set HF_TOKEN env)')
    p_up.add_argument('--direct', action='store_true',
                     help='Force direct Deployment mode (skip KServe)')
    p_up.add_argument('--context', default=None,
                     help='Target Kubernetes context')
    p_up.add_argument('--no-auto-install', action='store_true',
                     help='Skip auto-installing prerequisites')
    p_up.add_argument('--service-type', default=None,
                     choices=['ClusterIP', 'LoadBalancer', 'NodePort'],
                     help='K8s Service type for endpoint exposure '
                          '(overrides YAML spec)')
    p_up.set_defaults(func=cmd_up)

    # status
    p_status = subparsers.add_parser('status', help='Check service status')
    p_status.add_argument('service_name', nargs='?', default=None)
    p_status.add_argument('--namespace', default='skyserve-v2')
    p_status.add_argument('--context', default=None,
                         help='Target Kubernetes context')
    p_status.set_defaults(func=cmd_status)

    # down
    p_down = subparsers.add_parser('down', help='Tear down a service')
    p_down.add_argument('service_name')
    p_down.add_argument('--namespace', default='skyserve-v2')
    p_down.add_argument('--context', default=None,
                       help='Target Kubernetes context')
    p_down.set_defaults(func=cmd_down)

    # logs
    p_logs = subparsers.add_parser('logs', help='View service logs')
    p_logs.add_argument('service_name')
    p_logs.add_argument('--namespace', default='skyserve-v2')
    p_logs.add_argument('--tail', type=int, default=100)
    p_logs.add_argument('--replica', type=int, default=None)
    p_logs.add_argument('--context', default=None,
                       help='Target Kubernetes context')
    p_logs.set_defaults(func=cmd_logs)

    # endpoint
    p_ep = subparsers.add_parser('endpoint', help='Get service endpoint')
    p_ep.add_argument('service_name')
    p_ep.add_argument('--namespace', default='skyserve-v2')
    p_ep.add_argument('--context', default=None,
                     help='Target Kubernetes context')
    p_ep.set_defaults(func=cmd_endpoint)

    # prereqs
    p_prereqs = subparsers.add_parser('prereqs', help='Check prerequisites')
    p_prereqs.add_argument('--context', default=None,
                          help='Target Kubernetes context')
    p_prereqs.set_defaults(func=cmd_prereqs)

    # install
    p_install = subparsers.add_parser(
        'install', help='Install all prerequisites')
    p_install.add_argument('--context', default=None,
                          help='Target Kubernetes context')
    p_install.set_defaults(func=cmd_install)

    # generate
    p_gen = subparsers.add_parser('generate',
                                  help='Generate K8s resources (dry-run)')
    p_gen.add_argument('yaml_path')
    p_gen.add_argument('--namespace', default='skyserve-v2')
    p_gen.add_argument('--mode', choices=['kserve', 'direct'],
                      default='direct')
    p_gen.add_argument('--hf-token', default=None)
    p_gen.set_defaults(func=cmd_generate)

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    args.func(args)


if __name__ == '__main__':
    main()
