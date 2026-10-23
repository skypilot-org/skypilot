"""The `ray up` script is rendered with str.format() before it runs.

A brace anywhere in monkey_patch_ray_up.py (a dict literal, a comprehension)
is read as a replacement field and makes every `ray up` on the
RAY_AUTOSCALER path (IBM) fail before Ray runs.
"""
import os

from sky.backends import cloud_vm_ray_backend


def test_the_ray_up_script_renders_and_compiles(tmp_path):
    yaml_path = str(tmp_path / 'cluster.yaml')
    path = cloud_vm_ray_backend.write_ray_up_script_with_patched_launch_hash_fn(
        yaml_path, ray_up_kwargs={'no_restart': True})
    try:
        with open(path, 'r', encoding='utf-8') as f:
            script = f.read()
    finally:
        os.remove(path)
    compile(script, path, 'exec')
    assert repr(yaml_path) in script
    assert "'no_restart': True" in script
