"""Utilities for deprecating commands."""

import copy
import functools
from typing import Any, Dict, Optional

import click

from sky import sky_logging

logger = sky_logging.init_logger(__name__)


def _with_deprecation_warning(
        f,
        original_name: str,
        alias_name: str,
        override_command_argument: Optional[Dict[str, Any]] = None):

    @functools.wraps(f)
    def wrapper(self, *args, **kwargs):
        override_str = ''
        if override_command_argument is not None:
            overrides = []
            for k, v in override_command_argument.items():
                if isinstance(v, bool):
                    if v:
                        overrides.append(f'--{k}')
                    else:
                        overrides.append(f'--no-{k}')
                else:
                    overrides.append(f'--{k.replace("_", "-")}={v}')
            override_str = ' with additional arguments ' + ' '.join(overrides)
        click.secho(
            f'WARNING: `{alias_name}` has been renamed to `{original_name}` '
            f'and will be removed in a future release. Please use the '
            f'latter{override_str} instead.\n',
            err=True,
            fg='yellow')
        return f(self, *args, **kwargs)

    return wrapper


def _override_arguments(callback, override_command_argument: Dict[str, Any]):

    def wrapper(*args, **kwargs):
        logger.info(f'Overriding arguments: {override_command_argument}')
        kwargs.update(override_command_argument)
        return callback(*args, **kwargs)

    return wrapper


def _add_command_alias(
    group: click.Group,
    command: click.Command,
    hidden: bool = False,
    new_group: Optional[click.Group] = None,
    new_command_name: Optional[str] = None,
    override_command_argument: Optional[Dict[str, Any]] = None,
    with_warning: bool = True,
) -> None:
    """Add a alias of a command to a group."""
    if new_group is None:
        new_group = group
    if new_command_name is None:
        new_command_name = command.name
    if new_group == group and new_command_name == command.name:
        raise ValueError('Cannot add an alias to the same command.')
    new_command = copy.deepcopy(command)
    new_command.hidden = hidden
    new_command.name = new_command_name

    if override_command_argument:
        new_command.callback = _override_arguments(new_command.callback,
                                                   override_command_argument)

    orig = f'sky {group.name} {command.name}'
    alias = f'sky {new_group.name} {new_command_name}'
    if with_warning:
        new_command.invoke = _with_deprecation_warning(
            new_command.invoke,
            orig,
            alias,
            override_command_argument=override_command_argument)
    new_group.add_command(new_command, name=new_command_name)


def _deprecate_and_hide_command(group, command_to_deprecate,
                                alternative_command):
    """Hide a command and show a deprecation note, hinting the alternative."""
    command_to_deprecate.hidden = True
    if group is not None:
        orig = f'sky {group.name} {command_to_deprecate.name}'
    else:
        orig = f'sky {command_to_deprecate.name}'
    command_to_deprecate.invoke = _with_deprecation_warning(
        command_to_deprecate.invoke, alternative_command, orig)
