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
import concurrent.futures
import importlib
import os
import shlex
import shutil
import subprocess
import sys
import threading

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


# Serializes the per-target log writes: the pool has every patch shelling out
# at once, and the pod log is the only diagnostic this step has.
_LOG_LOCK = threading.Lock()


def _have_patch_binary() -> bool:
    # shutil.which rather than a shell `which`: minimal non-Debian images
    # (RHEL/UBI/Rocky) ship no `which` binary at all, and a failed lookup there
    # used to send us down the fallback with `patch` actually installed.
    return shutil.which('patch') is not None


def _ensure_patch_tooling() -> bool:
    """Installs what the patch step needs, once, before the pool starts.

    Returns whether the system `patch` binary is usable; when it is not, the
    pure-python fallback has been installed instead.

    Deciding this per target would have every thread race the same install:
    yum holds a global lock, so the losers fail and a thread can take the
    fallback while another is still installing `patch` -- applying .diff and
    .patch within one bootstrap -- and concurrent `pip install`s of a single
    distribution can leave it half-written.
    """
    if _have_patch_binary():
        return True
    # Quiet on the happy path -- an image with no yum says so on every launch --
    # but repeated below if we end up on the fallback, where it is the only clue
    # to why.
    proc = subprocess.run('sudo yum install -y patch',
                          shell=True,
                          check=False,
                          capture_output=True,
                          text=True)
    if _have_patch_binary():
        return True
    print(
        'No system `patch`, using the Python patch library instead. '
        f'`sudo yum install -y patch` said: {proc.stdout}{proc.stderr}'.strip())
    # Best-effort: a failure here surfaces at the `-m patch` call in
    # _run_patch, which is where it surfaced before this was hoisted.
    subprocess.run(f'{shlex.quote(sys.executable)} -m pip install patch',
                   shell=True,
                   check=False)
    return False


def _run_patch(target_file: str, patch_file: str, version: str,
               use_system_patch: bool) -> None:
    """Applies one patch, from the pristine .orig so it is safe to repeat."""
    # .orig is the original file that is not patched.
    orig_file = os.path.abspath(f'{target_file}-v{version}.orig')
    if use_system_patch:
        apply_cmd = f'patch {orig_file} -i {patch_file} -o {target_file}'
    else:
        # Invoke the fallback through sys.executable rather than a bare
        # `python`: the environment that owns the `ray` being patched is a venv
        # that may only expose `python3`, and sys.executable is by definition
        # the interpreter whose site-packages we are patching.
        diff_file = patch_file.replace('.patch', '.diff')
        apply_cmd = (f'{shlex.quote(sys.executable)} -m patch '
                     f'-d "$(dirname {target_file})" "{diff_file}"')
    script = f"""\
    if [ ! -f {orig_file} ]; then
        echo "Create backup file {orig_file}"
        cp {target_file} {orig_file}
    fi
    {apply_cmd}
    """
    proc = subprocess.run(script,
                          shell=True,
                          check=False,
                          capture_output=True,
                          text=True)
    name = os.path.basename(target_file)
    output = (proc.stdout + proc.stderr).strip()
    if output:
        with _LOG_LOCK:
            print(f'--- {name} ---\n{output}')
    if proc.returncode != 0:
        raise RuntimeError(f'Failed to patch {name} (exit {proc.returncode}): '
                           f'{output}')


def apply_patches(version: str) -> None:
    """Patches the Ray installed in this interpreter's environment.

    Args:
        version: the Ray version the patch files were generated against. Only
            used to name the .orig backups, so a later version bump does not
            reuse a backup taken from a different Ray.
    """
    # Resolve every target before patching any of them: importing Ray
    # submodules is the one shared-state step here, and a failure to locate a
    # file should not leave Ray half-patched.
    targets = []
    for module_name, patch_file in _PATCHES:
        module = importlib.import_module(module_name)
        target = getattr(module, '__file__', None)
        if target is None:
            raise RuntimeError(
                f'{module_name} has no __file__; cannot locate the installed '
                'Ray source to patch.')
        targets.append((target, _to_absolute(patch_file)))

    # Install the `patch` tooling once, not once per target -- see
    # _ensure_patch_tooling.
    use_system_patch = _ensure_patch_tooling()

    # Each patch shells out, and they touch disjoint files, so run them
    # concurrently -- this is on the critical path of every Kubernetes pod
    # bootstrap. Iterating the futures re-raises the first failure; the `with`
    # block then lets the rest finish before propagating, which is harmless
    # since _run_patch is idempotent.
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=len(targets)) as pool:
        futures = [
            pool.submit(_run_patch, target, patch_file, version,
                        use_system_patch) for target, patch_file in targets
        ]
        for future in futures:
            future.result()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ray-version', required=True)
    args = parser.parse_args()
    apply_patches(args.ray_version)


if __name__ == '__main__':
    main()
