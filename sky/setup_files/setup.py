# pylint: skip-file
"""SkyPilot.

SkyPilot is a framework for easily running machine learning* workloads on any
cloud through a unified interface. No knowledge of cloud offerings is required
or expected – you simply define the workload and its resource requirements, and
SkyPilot will automatically execute it on AWS, Google Cloud Platform or
Microsoft Azure.

*: SkyPilot is primarily targeted at machine learning workloads, but it can
also support many general workloads. We're excited to hear about your use case
and would love to hear more about how we can better support your requirements -
please join us in [this
discussion](https://github.com/skypilot-org/skypilot/discussions/1016)
"""
import atexit
import io
import os
import platform
import re
import runpy
import subprocess
import sys

import setuptools

# __file__ is setup.py at the root of the repo. We shouldn't assume it's a
# symlink - e.g. in the sdist it's resolved to a normal file.
ROOT_DIR = os.path.dirname(__file__)
DEPENDENCIES_FILE_PATH = os.path.join(ROOT_DIR, 'sky', 'setup_files',
                                      'dependencies.py')
INIT_FILE_PATH = os.path.join(ROOT_DIR, 'sky', '__init__.py')
_COMMIT_FAILURE_MESSAGE = (
    'WARNING: SkyPilot fail to {verb} the commit hash in '
    f'{INIT_FILE_PATH!r} (SkyPilot can still be normally used): '
    '{error}')

# setuptools does not include the script dir on the search path, so we can't
# just do `import dependencies`. Instead, use runpy to manually load it. Note:
# dependencies here is a dict, not a module, so we access it by subscripting.
dependencies = runpy.run_path(DEPENDENCIES_FILE_PATH)

original_init_content = None

system = platform.system()


# Keep in sync with sky/server/common.py get_skypilot_version_on_disk()
def find_version():
    # Extract version information from filepath
    # Adapted from:
    #  https://github.com/ray-project/ray/blob/master/python/setup.py
    with open(INIT_FILE_PATH, 'r', encoding='utf-8') as fp:
        version_match = re.search(r'^__version__ = [\'"]([^\'"]*)[\'"]',
                                  fp.read(), re.M)
        if version_match:
            return version_match.group(1)
        raise RuntimeError('Unable to find version string.')


def get_commit_hash():
    with open(INIT_FILE_PATH, 'r', encoding='utf-8') as fp:
        commit_match = re.search(r'^_SKYPILOT_COMMIT_SHA = [\'"]([^\'"]*)[\'"]',
                                 fp.read(), re.M)
        if commit_match:
            commit_hash = commit_match.group(1)
        else:
            raise RuntimeError('Unable to find commit string.')

    if 'SKYPILOT_COMMIT_SHA' not in commit_hash:
        return commit_hash
    try:
        # Getting the commit hash from git, and check if there are any
        # uncommitted changes.
        # TODO: keep this in sync with sky/__init__.py
        cwd = os.path.dirname(__file__)
        commit_hash = subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'],
            cwd=cwd,
            universal_newlines=True,
            stderr=subprocess.DEVNULL).strip()
        changes = subprocess.check_output(['git', 'status', '--porcelain'],
                                          cwd=cwd,
                                          universal_newlines=True,
                                          stderr=subprocess.DEVNULL).strip()
        if changes:
            commit_hash += '-dirty'
        return commit_hash
    except Exception as e:  # pylint: disable=broad-except
        print(_COMMIT_FAILURE_MESSAGE.format(verb='get', error=str(e)),
              file=sys.stderr)
        return commit_hash


def replace_commit_hash():
    """Fill in the commit hash in the __init__.py file."""
    try:
        with open(INIT_FILE_PATH, 'r', encoding='utf-8') as fp:
            content = fp.read()
            global original_init_content
            original_init_content = content
            content = re.sub(r'^_SKYPILOT_COMMIT_SHA = [\'"]([^\'"]*)[\'"]',
                             f'_SKYPILOT_COMMIT_SHA = \'{get_commit_hash()}\'',
                             content,
                             flags=re.M)
        with open(INIT_FILE_PATH, 'w', encoding='utf-8') as fp:
            fp.write(content)
    except Exception as e:  # pylint: disable=broad-except
        # Avoid breaking the installation when there is no permission to write
        # the file.
        print(_COMMIT_FAILURE_MESSAGE.format(verb='replace', error=str(e)),
              file=sys.stderr)
        pass


def revert_commit_hash():
    try:
        if original_init_content is not None:
            with open(INIT_FILE_PATH, 'w', encoding='utf-8') as fp:
                fp.write(original_init_content)
    except Exception as e:  # pylint: disable=broad-except
        # Avoid breaking the installation when there is no permission to write
        # the file.
        print(_COMMIT_FAILURE_MESSAGE.format(verb='replace', error=str(e)),
              file=sys.stderr)


def parse_readme(readme: str) -> str:
    """Parse the README.md file to be pypi compatible."""
    # Replace the footnotes.
    readme = readme.replace('<!-- Footnote -->', '#')
    footnote_re = re.compile(r'\[\^([0-9]+)\]')
    readme = footnote_re.sub(r'<sup>[\1]</sup>', readme)

    # Remove the dark mode switcher
    mode_re = re.compile(
        r'<picture>[\n ]*<source media=.*>[\n ]*<img(.*)>[\n ]*</picture>',
        re.MULTILINE)
    readme = mode_re.sub(r'<img\1>', readme)
    return readme


long_description = ''
readme_filepath = 'README.md'
# When sky/backends/wheel_utils.py builds wheels, it will not contain the
# README.  Skip the description for that case.
if os.path.exists(readme_filepath):
    long_description = io.open(readme_filepath, 'r', encoding='utf-8').read()
    long_description = parse_readme(long_description)

atexit.register(revert_commit_hash)
replace_commit_hash()

setuptools.setup(
    # NOTE: this affects the package.whl wheel name. When changing this (if
    # ever), you must grep for '.whl' and change all corresponding wheel paths
    # (templates/*.j2 and wheel_utils.py).
    name='skypilot',
    version=find_version(),
    packages=setuptools.find_packages(),
    author='SkyPilot Team',
    license='Apache 2.0',
    readme='README.md',
    description='SkyPilot: Run AI on Any Infra — Unified, Faster, Cheaper.',
    long_description=long_description,
    long_description_content_type='text/markdown',
    setup_requires=['wheel'],
    requires_python='>=3.7',
    install_requires=dependencies['install_requires'],
    extras_require=dependencies['extras_require'],
    entry_points={
        'console_scripts': ['sky = sky.cli:cli'],
    },
    include_package_data=True,
    classifiers=[
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'License :: OSI Approved :: Apache Software License',
        'Operating System :: OS Independent',
        'Topic :: Software Development :: Libraries :: Python Modules',
        'Topic :: System :: Distributed Computing',
    ],
    project_urls={
        'Homepage': 'https://github.com/skypilot-org/skypilot',
        'Issues': 'https://github.com/skypilot-org/skypilot/issues',
        'Discussion': 'https://github.com/skypilot-org/skypilot/discussions',
        'Documentation': 'https://docs.skypilot.co/',
    },
)
