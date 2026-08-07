"""Drift guard for the inlined ray_port encode script in instance_setup.

The remote ray-status command embeds a stdlib-only copy of
``message_utils.encode_payload`` (built via ``inspect.getsource``) so the head
pod doesn't pay the ~2s ``import sky`` tax to encode a single integer. This
test executes that inlined script in a fresh interpreter and asserts the wire
format matches what ``encode_payload`` produces — so the next time anyone
changes ``encode_payload`` in a way the inlining can't handle (new module-level
state, an annotation that references ``sky.*``, etc.), CI fails here.
"""
import os
import subprocess
import sys

from sky.provision import instance_setup
from sky.utils import message_utils


def _run_inlined_script(ray_port: int) -> str:
    env = {**os.environ, 'RAY_PORT': str(ray_port)}
    return subprocess.check_output(
        [sys.executable, '-c', instance_setup._RAY_PORT_ENCODE_SCRIPT],
        env=env,
        text=True,
    )


def test_inlined_encode_matches_real_encode_payload():
    """The inlined script must produce the same bytes as
    ``print(encode_payload(...))``."""
    for port in (6380, 6379, 12345):
        out = _run_inlined_script(port)
        # The remote runs ``print(encode_payload(...))``, so the captured
        # stdout has encode_payload's trailing newline plus print's newline.
        expected = message_utils.encode_payload({'ray_port': port}) + '\n'
        assert out == expected, (
            f'inlined script drift for port={port}:\n'
            f'  got      : {out!r}\n  expected : {expected!r}')


def test_inlined_encode_decodes_with_decode_payload():
    """``decode_payload`` (consumer in provisioner.py) must accept the
    inlined script's output and recover the original port."""
    for port in (6380, 6379, 12345):
        out = _run_inlined_script(port)
        decoded = message_utils.decode_payload(out)
        assert decoded == {'ray_port': port}, decoded


def test_inlined_script_does_not_import_sky():
    """The inlined script must not contain ``import sky`` or
    ``from sky``: that's the entire point of inlining."""
    script = instance_setup._RAY_PORT_ENCODE_SCRIPT
    assert 'import sky' not in script, script
    assert 'from sky' not in script, script
