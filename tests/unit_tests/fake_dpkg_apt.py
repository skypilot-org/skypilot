"""A minimal dpkg and apt-get emulation for container setup tests.

Run as ``python fake_dpkg_apt.py <tool> <args...>`` where ``<tool>`` is
``dpkg-query``, ``dpkg`` or ``apt-get``. Thin ``sh`` wrappers named after the
tools put it on PATH. It keeps a package database in a JSON file, so a test
can seed an image state, run a real shell script against it, and then check
the resulting state instead of the commands the script happened to run.

Only the behaviour the SkyPilot container setup relies on is emulated:

* ``dpkg-query -W [-f FORMAT] PKG...`` and ``dpkg -s PKG`` / ``dpkg -l PKG``
  report package status. A package dpkg has never heard of makes the tool
  exit 1; one it knows but never installed reports
  ``unknown ok not-installed`` with exit 0, as real dpkg does for packages
  present in the apt index. A removed-but-not-purged package reports
  ``deinstall ok config-files``.
* ``apt-get [OPTIONS] update`` succeeds unless SKY_FAKE_APT_UPDATE_FAILS is
  set. ``apt-get [OPTIONS] install PKG...`` installs the packages with
  their dependencies from ``_DEPENDS``. Packages listed in
  SKY_FAKE_APT_UNAVAILABLE cannot be located and fail the call.
* ``fuse`` and ``fuse3`` break each other, as on Ubuntu 22.04+ and Debian
  12: asking for both in one call fails, and installing one removes the
  other (its library dependency stays, so ``libfuse2`` survives a later
  ``fuse3`` install).
* Installing the ``python3`` package creates a ``python3`` executable in
  SKY_FAKE_BIN, so ``command -v python3`` sees it afterwards.

Environment: SKY_FAKE_DPKG_DB (JSON file: package name -> dpkg status),
SKY_FAKE_APT_LOG (one JSON object per apt-get call), SKY_FAKE_BIN.
"""
import json
import os
import sys
from typing import Dict, List

INSTALLED = 'install ok installed'
CONFIG_FILES = 'deinstall ok config-files'
NOT_INSTALLED = 'unknown ok not-installed'

_DEPENDS = {
    'python3-pip': ['python3', 'python3-dev'],
    'fuse': ['libfuse2'],
    'fuse3': ['libfuse3-3'],
}
_BREAKS = {'fuse': 'fuse3', 'fuse3': 'fuse'}


def _load() -> Dict[str, str]:
    with open(os.environ['SKY_FAKE_DPKG_DB'], encoding='utf-8') as f:
        return json.load(f)


def _save(db: Dict[str, str]) -> None:
    with open(os.environ['SKY_FAKE_DPKG_DB'], 'w', encoding='utf-8') as f:
        json.dump(db, f)


def _dpkg_query(args: List[str]) -> int:
    fmt = '${Package}\t${Version}\n'
    packages = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ('-f', '--showformat'):
            fmt = args[i + 1]
            i += 2
            continue
        if arg.startswith('-f=') or arg.startswith('--showformat='):
            fmt = arg.split('=', 1)[1]
        elif not arg.startswith('-'):
            packages.append(arg)
        i += 1
    db = _load()
    rc = 0
    for pkg in packages:
        if pkg not in db:
            print(f'dpkg-query: no packages found matching {pkg}',
                  file=sys.stderr)
            rc = 1
            continue
        out = fmt.replace('${Package}', pkg).replace('${Status}', db[pkg])
        out = out.replace('${Version}', '1.0').replace('\\n', '\n')
        sys.stdout.write(out)
    return rc


def _dpkg(args: List[str]) -> int:
    db = _load()
    mode = args[0]
    rc = 0
    for pkg in args[1:]:
        if pkg not in db:
            print(f'dpkg: package {pkg} is not installed', file=sys.stderr)
            rc = 1
        elif mode in ('-s', '--status'):
            print(f'Package: {pkg}\nStatus: {db[pkg]}\nVersion: 1.0')
        elif mode in ('-l', '--list'):
            abbrev = {INSTALLED: 'ii', CONFIG_FILES: 'rc'}.get(db[pkg], 'un')
            print(f'{abbrev}  {pkg}  1.0  all  fake')
    return rc


def _apt_get(args: List[str]) -> int:
    positional = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '-o' or (arg.startswith('-') and arg[-1] == 'o' and
                           not arg.startswith('--')):
            i += 2  # `-o Key=Value` and combined forms like `-yo`.
            continue
        if not arg.startswith('-'):
            positional.append(arg)
        i += 1
    if not positional:
        print('E: apt-get needs a command', file=sys.stderr)
        return 1
    command, packages = positional[0], positional[1:]
    with open(os.environ['SKY_FAKE_APT_LOG'], 'a', encoding='utf-8') as f:
        f.write(json.dumps({'command': command, 'packages': packages}) + '\n')

    if command == 'update':
        if os.environ.get('SKY_FAKE_APT_UPDATE_FAILS'):
            print('E: Failed to fetch the package index', file=sys.stderr)
            return 100
        return 0
    if command != 'install':
        return 0

    unavailable = os.environ.get('SKY_FAKE_APT_UNAVAILABLE', '').split()
    for pkg in packages:
        if pkg in unavailable:
            print(f'E: Unable to locate package {pkg}', file=sys.stderr)
            return 100
    if 'fuse' in packages and 'fuse3' in packages:
        print('E: Unable to correct problems, you have held broken packages.',
              file=sys.stderr)
        return 100

    db = _load()
    for pkg in packages:
        broken = _BREAKS.get(pkg)
        if broken is not None and db.get(broken) == INSTALLED:
            db[broken] = CONFIG_FILES
        for name in [pkg] + _DEPENDS.get(pkg, []):
            db[name] = INSTALLED
            if name == 'python3':
                path = os.path.join(os.environ['SKY_FAKE_BIN'], 'python3')
                with open(path, 'w', encoding='utf-8') as f:
                    f.write('#!/bin/sh\nexit 0\n')
                os.chmod(path, 0o755)
    _save(db)
    return 0


def main() -> int:
    tool, args = sys.argv[1], sys.argv[2:]
    if tool == 'dpkg-query':
        return _dpkg_query(args)
    if tool == 'dpkg':
        return _dpkg(args)
    if tool == 'apt-get':
        return _apt_get(args)
    print(f'fake_dpkg_apt: unknown tool {tool}', file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main())
