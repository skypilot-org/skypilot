"""Applies SkyPilot's Ray patches to an installed Ray.

Stdlib only, and deliberately free of any ``sky`` import, so this can also run
straight from an unpacked copy of this directory. The Kubernetes bootstrap
needs that: it patches Ray from the pod's own args, before the SkyPilot wheel
that decides the Ray version has been uploaded, so the patches have to travel
with the pod spec rather than with whatever ``pip install skypilot`` happens to
resolve to.

Run standalone as:

    python apply_patches.py --ray-version 2.9.3
"""
import argparse
import importlib
import os
import shlex
import subprocess
import sys

# (module to import, patch file in this directory). The module is imported to
# locate the installed file; the whole thing runs in a throwaway process
# because an imported Ray module would otherwise stay in memory unpatched.
_PATCHES = [
    ('ray._private.log_monitor', 'log_monitor.py.patch'),
    ('ray._private.worker', 'worker.py.patch'),
    ('ray.dashboard.modules.job.cli', 'cli.py.patch'),
    ('ray.autoscaler._private.autoscaler', 'autoscaler.py.patch'),
    ('ray.autoscaler._private.command_runner', 'command_runner.py.patch'),
    ('ray.autoscaler._private.resource_demand_scheduler',
     'resource_demand_scheduler.py.patch'),
    ('ray.autoscaler._private.updater', 'updater.py.patch'),
]


def _to_absolute(pwd_file: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), pwd_file)


def _run_patch(target_file: str, patch_file: str, version: str) -> None:
    """Applies a patch if it has not been applied already."""
    # .orig is the original file that is not patched.
    orig_file = os.path.abspath(f'{target_file}-v{version}.orig')
    # Get diff filename by replacing .patch with .diff
    diff_file = patch_file.replace('.patch', '.diff')

    # Detect `patch` with `command -v` (a POSIX shell builtin) rather than
    # `which` (a separate binary that minimal non-Debian images -- RHEL/UBI/
    # Rocky -- do not ship). With `which`, `which patch` fails on those images
    # even when `patch` IS installed, so we silently took the Python fallback
    # below and lost the Ray patches.
    #
    # Invoke the fallback through sys.executable rather than a bare `python`:
    # the environment that owns the `ray` being patched is a venv that may only
    # expose `python3` (or expose neither on PATH), and sys.executable is by
    # definition the interpreter whose site-packages we are patching.
    py = shlex.quote(sys.executable)
    script = f"""\
    command -v patch >/dev/null 2>&1 || sudo yum install -y patch || true
    if [ ! -f {orig_file} ]; then
        echo Create backup file {orig_file}
        cp {target_file} {orig_file}
    fi
    if command -v patch >/dev/null 2>&1; then
        # System patch command is available, use it
        # It is ok to patch again from the original file.
        patch {orig_file} -i {patch_file} -o {target_file}
    else
        # System patch command not available, use Python patch library
        echo "System patch command not available, using Python patch library..."
        {py} -m pip install patch
        # Get target directory
        target_dir="$(dirname {target_file})"
        # Execute python patch command
        echo "Executing {py} -m patch -d $target_dir {diff_file}"
        {py} -m patch -d "$target_dir" "{diff_file}"
    fi
    """
    subprocess.run(script, shell=True, check=True)


def apply_patches(version: str) -> None:
    """Patches the Ray installed in this interpreter's environment.

    Args:
        version: the Ray version the patch files were generated against. Only
            used to name the .orig backups, so a later version bump does not
            reuse a backup taken from a different Ray.
    """
    for module_name, patch_file in _PATCHES:
        module = importlib.import_module(module_name)
        target = getattr(module, '__file__', None)
        if target is None:
            raise RuntimeError(
                f'{module_name} has no __file__; cannot locate the installed '
                'Ray source to patch.')
        _run_patch(target, _to_absolute(patch_file), version)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ray-version', required=True)
    args = parser.parse_args()
    apply_patches(args.ray_version)


if __name__ == '__main__':
    main()
