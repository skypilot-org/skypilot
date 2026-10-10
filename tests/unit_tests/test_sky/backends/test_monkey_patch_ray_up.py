"""Tests for the `ray up` monkey patches.

The patched code itself only runs under ProvisionerVersion.RAY_AUTOSCALER --
IBM alone -- so nothing here provisions anything. What it does cover is the
contract that path depends on: that the names SkyPilot patches are still the
names Ray calls, and that the launch hash delegates to Ray instead of copying
it. Both fail silently at IBM launch time otherwise.

Scope: this checks the Ray in *this* environment, which is the right target --
`ray up` runs against whatever Ray the client has, and dependencies.py pins
only `ray[default] >= 2.6.1`. It is therefore not evidence about
SKY_REMOTE_RAY_VERSION specifically; the version it ran against is reported by
test_which_ray_these_checks_ran_against so a green run cannot be mistaken for
one.
"""
import ast
import os
from typing import Any, Callable, Dict

import pytest

ray = pytest.importorskip('ray', reason='ray is an optional dependency')
ray_version = ray.__version__

# pylint: disable=wrong-import-position
from ray.autoscaler import sdk  # noqa: E402
from ray.autoscaler._private import util as ray_autoscaler_util

# tests/unit_tests/test_sky/backends -> repo root
ROOT_DIR = os.path.dirname(
    os.path.dirname(
        os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
SCRIPT = os.path.join(ROOT_DIR, 'sky', 'backends', 'monkey_patches',
                      'monkey_patch_ray_up.py')

NODE_CONF = {'InstanceType': 'fake-type'}
AUTH = {'ssh_user': 'sky'}


def _load_patched_hash(util: Any = ray_autoscaler_util) -> Callable[..., Any]:
    """Lift the one function out of the script's AST.

    The script cannot be imported: it is a template filled in at `ray up` time,
    and importing it would run `ray up`. `util` is injected so a test can watch
    what actually reaches Ray.
    """
    with open(SCRIPT, 'r', encoding='utf-8') as f:
        source = f.read()
    for node in ast.parse(source).body:
        if (isinstance(node, ast.FunctionDef) and
                node.name == 'monkey_patch_hash_launch_conf'):
            namespace: Dict[str, Any] = {'ray_autoscaler_util': util}
            exec(  # pylint: disable=exec-used
                compile(ast.Module(body=[node], type_ignores=[]), SCRIPT,
                        'exec'), namespace)
            return namespace['monkey_patch_hash_launch_conf']
    raise AssertionError(f'monkey_patch_hash_launch_conf not found in {SCRIPT}')


class _RecordingUtil:
    """Stands in for ray.autoscaler._private.util, recording what it is given."""

    def __init__(self):
        self.calls = []

    def hash_launch_conf(self, node_conf, auth):
        self.calls.append((node_conf, auth))
        return 'delegated'


def test_which_ray_these_checks_ran_against():
    """Name the version, so a pass is not read as a pass against every Ray."""
    print(f'monkey-patch contract checked against ray {ray_version}')
    assert ray_version


def test_the_patch_targets_names_ray_still_calls():
    """The script assigns onto these; a rename makes the patch a silent no-op."""
    commands = sdk.sdk.commands
    for name in ('hash_launch_conf', '_should_create_new_head'):
        assert hasattr(commands, name), (
            f'ray.autoscaler._private.commands no longer exposes {name}; the '
            'monkey patch would assign a name nothing reads')


def test_the_hash_delegates_instead_of_reimplementing():
    """A copy silently disagrees with the hash Ray's own node_launcher writes.

    Asserted as "Ray's function is what produced the answer", not as equality
    against Ray's output: Ray has already changed this digest once
    (sha1/hexdigest -> sha256/base32hex), so a stale copy matches whenever the
    installed Ray happens to predate the change -- which is exactly when the
    test is needed least.
    """
    util = _RecordingUtil()
    assert _load_patched_hash(util)(NODE_CONF, AUTH) == 'delegated'
    assert len(util.calls) == 1


def test_ssh_proxy_command_never_reaches_the_hash():
    """The whole reason SkyPilot patches this: the proxy command can change
    without the node needing to be relaunched."""
    util = _RecordingUtil()
    patched = _load_patched_hash(util)
    patched(NODE_CONF, dict(AUTH, ssh_proxy_command='ssh -W %h:%p jump'))
    (_, auth), = util.calls
    assert 'ssh_proxy_command' not in auth
    assert auth == AUTH, 'the patch dropped more than ssh_proxy_command'


def test_the_patch_does_not_recurse(monkeypatch):
    """It reads util's copy, while the script overwrites commands' copy.

    Sourcing it from the patched name instead would recurse until the stack
    blew -- on IBM, at launch time.
    """
    patched = _load_patched_hash()

    def _boom(*args, **kwargs):
        raise AssertionError('called the patched name, not Ray\'s own')

    monkeypatch.setattr(sdk.sdk.commands, 'hash_launch_conf', _boom)
    assert patched(NODE_CONF, AUTH)
