"""Flags for the CLI."""

import os
from typing import Optional, Tuple

import click
import dotenv

from sky import skypilot_config
from sky.skylet import autostop_lib
from sky.utils import resources_utils


def _parse_env_var(env_var: str) -> Tuple[str, str]:
    """Parse env vars into a (KEY, VAL) pair."""
    if '=' not in env_var:
        value = os.environ.get(env_var)
        if value is None:
            raise click.UsageError(
                f'{env_var} is not set in local environment.')
        return (env_var, value)
    ret = tuple(env_var.split('=', 1))
    if len(ret) != 2:
        raise click.UsageError(
            f'Invalid env var: {env_var}. Must be in the form of KEY=VAL '
            'or KEY.')
    return ret[0], ret[1]


def _parse_secret_var(secret_var: str) -> Tuple[str, str]:
    """Parse secret vars into a (KEY, VAL) pair."""
    if '=' not in secret_var:
        value = os.environ.get(secret_var)
        if value is None:
            raise click.UsageError(
                f'{secret_var} is not set in local environment.')
        return (secret_var, value)
    ret = tuple(secret_var.split('=', 1))
    if len(ret) != 2:
        raise click.UsageError(
            f'Invalid secret var: {secret_var}. Must be in the form of KEY=VAL '
            'or KEY.')
    return ret[0], ret[1]


COMMON_OPTIONS = [
    click.option('--async/--no-async',
                 'async_call',
                 required=False,
                 is_flag=True,
                 default=False,
                 help=('Run the command asynchronously.'))
]

GRACEFUL_OPTIONS = [
    click.option(
        '--graceful',
        is_flag=True,
        default=False,
        help=('Wait for MOUNT_CACHED uploads to complete before '
              'stopping/terminating. Will cancel current jobs first.')),
    click.option('--graceful-timeout',
                 type=int,
                 default=None,
                 help=('Timeout in seconds for `--graceful` flag. When not '
                       'set, will wait for MOUNT_CACHED uploads until they are '
                       'finished.')),
]

TASK_OPTIONS = [
    click.option(
        '--workdir',
        required=False,
        type=click.Path(exists=True, file_okay=False),
        help=('If specified, sync this dir to the remote working directory, '
              'where the task will be invoked. '
              'Overrides the "workdir" config in the YAML if both are supplied.'
             )),
    click.option(
        '--infra',
        required=False,
        type=str,
        help='Infrastructure to use. '
        'Format: cloud, cloud/region, cloud/region/zone, '
        'k8s/context-name, or ssh/node-pool-name. '
        'Examples: aws, aws/us-east-1, aws/us-east-1/us-east-1a, '
        # TODO(zhwu): we have to use `\*` to make sure the docs build
        # not complaining about the `*`, but this will cause `--help`
        # to show `\*` instead of `*`.
        'aws/\\*/us-east-1a, k8s/my-context, ssh/my-nodes.'),
    click.option(
        '--cloud',
        required=False,
        type=str,
        help=('The cloud to use. If specified, overrides the "resources.cloud" '
              'config. Passing "none" resets the config.'),
        hidden=True),
    click.option(
        '--region',
        required=False,
        type=str,
        help=('The region to use. If specified, overrides the '
              '"resources.region" config. Passing "none" resets the config.'),
        hidden=True),
    click.option(
        '--zone',
        required=False,
        type=str,
        help=('The zone to use. If specified, overrides the '
              '"resources.zone" config. Passing "none" resets the config.'),
        hidden=True),
    click.option(
        '--num-nodes',
        required=False,
        type=int,
        help=('Number of nodes to execute the task on. '
              'Overrides the "num_nodes" config in the YAML if both are '
              'supplied.')),
    click.option(
        '--cpus',
        default=None,
        type=str,
        required=False,
        help=('Number of vCPUs each instance must have (e.g., '
              '``--cpus=4`` (exactly 4) or ``--cpus=4+`` (at least 4)). '
              'This is used to automatically select the instance type.')),
    click.option(
        '--memory',
        default=None,
        type=str,
        required=False,
        help=(
            'Amount of memory each instance must have in GB (e.g., '
            '``--memory=16`` (exactly 16GB), ``--memory=16+`` (at least 16GB))'
        )),
    click.option('--disk-size',
                 default=None,
                 type=int,
                 required=False,
                 help=('OS disk size in GBs.')),
    click.option('--disk-tier',
                 default=None,
                 type=click.Choice(resources_utils.DiskTier.supported_tiers(),
                                   case_sensitive=False),
                 required=False,
                 help=resources_utils.DiskTier.cli_help_message()),
    click.option('--network-tier',
                 default=None,
                 type=click.Choice(
                     resources_utils.NetworkTier.supported_tiers(),
                     case_sensitive=False),
                 required=False,
                 help=resources_utils.NetworkTier.cli_help_message()),
    click.option(
        '--use-spot/--no-use-spot',
        required=False,
        default=None,
        help=('Whether to request spot instances. If specified, overrides the '
              '"resources.use_spot" config.')),
    click.option('--image-id',
                 required=False,
                 default=None,
                 help=('Custom image id for launching the instances. '
                       'Passing "none" resets the config.')),
    click.option('--env-file',
                 required=False,
                 type=dotenv.dotenv_values,
                 help="""\
        Path to a dotenv file with environment variables to set on the remote
        node.

        If any values from ``--env-file`` conflict with values set by
        ``--env``, the ``--env`` value will be preferred.

        Values from ``--env-file`` will also load to secrets with lower
        preference compared to ``--secret`` or ``--secret-file``.
        """),
    click.option(
        '--env',
        required=False,
        type=_parse_env_var,
        multiple=True,
        help="""\
        Environment variable to set on the remote node.
        It can be specified multiple times.
        Examples:

        \b
        1. ``--env MY_ENV=1``: set ``$MY_ENV`` on the cluster to be 1.

        2. ``--env MY_ENV2=$HOME``: set ``$MY_ENV2`` on the cluster to be the
        same value of ``$HOME`` in the local environment where the CLI command
        is run.

        3. ``--env MY_ENV3``: set ``$MY_ENV3`` on the cluster to be the
        same value of ``$MY_ENV3`` in the local environment.""",
    ),
    click.option(
        '--secret-file',
        required=False,
        type=dotenv.dotenv_values,
        help="""\
        Path to a dotenv file with secret variables to set on the remote node.

        If any values from ``--secret-file`` conflict with values set by
        ``--secret``, the ``--secret`` value will be preferred.""",
    ),
    click.option(
        '--secret',
        required=False,
        type=_parse_secret_var,
        multiple=True,
        help="""\
        Secret variable to set on the remote node. These variables will be
        redacted in logs and YAML outputs for security. It can be specified
        multiple times. Examples:

        \b
        1. ``--secret API_KEY=secret123``: set ``$API_KEY`` on the cluster to
        be secret123.

        2. ``--secret JWT_SECRET``: set ``$JWT_SECRET`` on the cluster to be
        the same value of ``$JWT_SECRET`` in the local environment.""",
    )
]

TASK_OPTIONS_WITH_NAME = [
    click.option('--name',
                 '-n',
                 required=False,
                 type=str,
                 help=('Task name. Overrides the "name" '
                       'config in the YAML if both are supplied.')),
] + TASK_OPTIONS

EXTRA_RESOURCES_OPTIONS = [
    click.option(
        '--gpus',
        required=False,
        type=str,
        help=
        ('Type and number of GPUs to use. Example values: '
         '"V100:8", "V100" (short for a count of 1), or "V100:0.5" '
         '(fractional counts are supported by the scheduling framework). '
         'If a new cluster is being launched by this command, this is the '
         'resources to provision. If an existing cluster is being reused, this'
         ' is seen as the task demand, which must fit the cluster\'s total '
         'resources and is used for scheduling the task. '
         'Overrides the "accelerators" '
         'config in the YAML if both are supplied. '
         'Passing "none" resets the config.')),
    click.option(
        '--instance-type',
        '-t',
        required=False,
        type=str,
        help=('The instance type to use. If specified, overrides the '
              '"resources.instance_type" config. Passing "none" resets the '
              'config.'),
    ),
    click.option(
        '--ports',
        required=False,
        type=str,
        multiple=True,
        help=('Ports to open on the cluster. '
              'If specified, overrides the "ports" config in the YAML. '),
    ),
]


def config_option(expose_value: bool):
    """A decorator for the --config option.

    This decorator is used to parse the --config option.

    Any overrides specified in the command line will be applied to the skypilot
    config before the decorated function is called.

    If expose_value is True, the decorated function will receive the parsed
    config overrides as 'config_override' parameter.

    Args:
        expose_value: Whether to expose the value of the option to the decorated
            function.
    """

    def preprocess_config_options(ctx, param, value):
        del ctx  # Unused.
        param.name = 'config_override'
        try:
            if len(value) == 0:
                return None
            else:
                # Apply the config overrides to the skypilot config.
                return skypilot_config.apply_cli_config(value)
        except ValueError as e:
            raise click.BadParameter(f'{str(e)}') from e

    def return_option_decorator(func):
        return click.option(
            '--config',
            required=False,
            type=str,
            multiple=True,
            expose_value=expose_value,
            callback=preprocess_config_options,
            help=('Path to a config file or a single key-value pair. To add '
                  'multiple key-value pairs add multiple flags (e.g. '
                  '--config nested.key1=val1 --config nested.key2=val2).'),
        )(func)

    return return_option_decorator


def yes_option(helptext: Optional[str] = None):
    """A decorator for the --yes/-y option."""
    if helptext is None:
        helptext = 'Skip confirmation prompt.'

    def return_option_decorator(func):
        return click.option('--yes',
                            '-y',
                            is_flag=True,
                            default=False,
                            required=False,
                            help=helptext)(func)

    return return_option_decorator


def verbose_option(helptext: Optional[str] = None):
    """A decorator for the --verbose/-v option."""

    if helptext is None:
        helptext = 'Show all information in full.'

    def return_option_decorator(func):
        return click.option('--verbose',
                            '-v',
                            default=False,
                            is_flag=True,
                            required=False,
                            help=helptext)(func)

    return return_option_decorator


def all_option(helptext: Optional[str] = None):
    """A decorator for the --all option."""

    def return_option_decorator(func):
        return click.option('--all',
                            '-a',
                            is_flag=True,
                            default=False,
                            required=False,
                            help=helptext)(func)

    return return_option_decorator


def all_users_option(helptext: Optional[str] = None):
    """A decorator for the --all-users option."""

    def return_option_decorator(func):
        return click.option('--all-users',
                            '-u',
                            is_flag=True,
                            default=False,
                            required=False,
                            help=helptext)(func)

    return return_option_decorator


def wait_for_option(pair: str):
    """A decorator for the --wait-for option."""

    def return_option_decorator(func):
        return click.option(
            '--wait-for',
            type=click.Choice(autostop_lib.AutostopWaitFor.supported_modes()),
            default=None,
            required=False,
            help=autostop_lib.AutostopWaitFor.cli_help_message(pair=pair))(func)

    return return_option_decorator
