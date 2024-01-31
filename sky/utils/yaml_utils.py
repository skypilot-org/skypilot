"""This module contains a custom yaml constructor for
loading yaml with environment variables.
"""
import os
import re

import yaml

path_matcher = re.compile(r'\$\{([^}^{]+)\}')


def path_constructor(loader, node):
    del loader
    value = node.value
    match = path_matcher.match(value)
    env_var = match.group()[2:-1]
    return os.environ.get(env_var) + value[match.end():]


yaml.add_implicit_resolver('!path', path_matcher, None, yaml.SafeLoader)
yaml.add_constructor('!path', path_constructor, yaml.SafeLoader)


def yaml_safe_load_with_env(file_path):
    return yaml.safe_load(file_path)


def yaml_safe_load_all_with_env(file_path):
    return yaml.safe_load_all(file_path)
