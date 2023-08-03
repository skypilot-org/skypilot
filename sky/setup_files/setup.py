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

import io
import os
import platform
import re
import warnings
from typing import Dict, List

import setuptools

ROOT_DIR = os.path.dirname(__file__)

system = platform.system()
if system == 'Darwin':
    mac_version = platform.mac_ver()[0]
    mac_major, mac_minor = mac_version.split('.')[:2]
    mac_major = int(mac_major)
    mac_minor = int(mac_minor)
    if mac_major < 10 or (mac_major == 10 and mac_minor < 15):
        warnings.warn(
            f'\'Detected MacOS version {mac_version}. MacOS version >=10.15 '
            'is required to install ray>=1.9\'')


def find_version(*filepath):
    # Extract version information from filepath
    # Adapted from:
    #  https://github.com/ray-project/ray/blob/master/python/setup.py
    with open(os.path.join(ROOT_DIR, *filepath)) as fp:
        version_match = re.search(r'^__version__ = [\'"]([^\'"]*)[\'"]',
                                  fp.read(), re.M)
        if version_match:
            return version_match.group(1)
        raise RuntimeError('Unable to find version string.')


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


install_requires = [
    'wheel',
    # NOTE: ray requires click>=7.0. Also, click 8.1.x makes our rendered CLI
    # docs display weird blockquotes.
    # TODO(zongheng): investigate how to make click 8.1.x display nicely and
    # remove the upper bound.
    'click<=8.0.4,>=7.0',
    # NOTE: required by awscli. To avoid ray automatically installing
    # the latest version.
    'colorama<0.4.5',
    'cryptography',
    # Jinja has a bug in older versions because of the lack of pinning
    # the version of the underlying markupsafe package. See:
    # https://github.com/pallets/jinja/issues/1585
    'jinja2>=3.0',
    'jsonschema',
    'networkx',
    'oauth2client',
    'pandas',
    'pendulum',
    # PrettyTable with version >=2.0.0 is required for the support of
    # `add_rows` method.
    'PrettyTable>=2.0.0',
    'python-dotenv',
    # Lower version of ray will cause dependency conflict for
    # click/grpcio/protobuf.
    'ray[default]>=2.2.0,<=2.4.0',
    'rich',
    'tabulate',
    # Light weight requirement, can be replaced with "typing" once
    # we deprecate Python 3.7 (this will take a while).
    "typing_extensions; python_version < '3.8'",
    'filelock>=3.6.0',
    # Adopted from ray's setup.py: https://github.com/ray-project/ray/blob/ray-2.4.0/python/setup.py
    # SkyPilot: != 1.48.0 is required to avoid the error where ray dashboard fails to start when
    # ray start is called (#2054).
    # Tracking issue: https://github.com/ray-project/ray/issues/30984
    "grpcio >= 1.32.0, <= 1.49.1, != 1.48.0; python_version < '3.10' and sys_platform == 'darwin'",  # noqa:E501
    "grpcio >= 1.42.0, <= 1.49.1, != 1.48.0; python_version >= '3.10' and sys_platform == 'darwin'",  # noqa:E501
    # Original issue: https://github.com/ray-project/ray/issues/33833
    "grpcio >= 1.32.0, <= 1.51.3, != 1.48.0; python_version < '3.10' and sys_platform != 'darwin'",  # noqa:E501
    "grpcio >= 1.42.0, <= 1.51.3, != 1.48.0; python_version >= '3.10' and sys_platform != 'darwin'",  # noqa:E501
    'packaging',
    # Adopted from ray's setup.py:
    # https://github.com/ray-project/ray/blob/86fab1764e618215d8131e8e5068f0d493c77023/python/setup.py#L326
    'protobuf >= 3.15.3, != 3.19.5',
    'psutil',
    'pulp',
    # Ray job has an issue with pydantic>2.0.0, due to API changes of pydantic. See
    # https://github.com/ray-project/ray/issues/36990
    'pydantic<2.0',
    # Cython 3.0 release breaks PyYAML installed by aws-cli.
    # https://github.com/yaml/pyyaml/issues/601
    # https://github.com/aws/aws-cli/issues/8036
    # <= 3.13 may encounter https://github.com/ultralytics/yolov5/issues/414
    'pyyaml > 3.13, <= 5.3.1'
]

# NOTE: Change the templates/spot-controller.yaml.j2 file if any of the
# following packages dependencies are changed.
aws_dependencies = [
    # botocore does not work with urllib3>=2.0.0, accuroding to https://github.com/boto/botocore/issues/2926
    # We have to explicitly pin the version to optimize the time for
    # poetry install. See https://github.com/orgs/python-poetry/discussions/7937
    'urllib3<2',
    # NOTE: this installs CLI V1. To use AWS SSO (e.g., `aws sso login`), users
    # should instead use CLI V2 which is not pip-installable. See
    # https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html.
    'awscli>=1.27.10',
    'botocore>=1.29.10',
    'boto3>=1.26.1',
    # 'Crypto' module used in authentication.py for AWS.
    'pycryptodome==3.12.0',
]
extras_require: Dict[str, List[str]] = {
    'aws': aws_dependencies,
    # TODO(zongheng): azure-cli is huge and takes a long time to install.
    # Tracked in: https://github.com/Azure/azure-cli/issues/7387
    # azure-identity is needed in node_provider.
    # We need azure-identity>=1.13.0 to enable the customization of the
    # timeout of AzureCliCredential.
    'azure': [
        'azure-cli>=2.31.0', 'azure-core', 'azure-identity>=1.13.0',
        'azure-mgmt-network'
    ],
    'gcp': ['google-api-python-client', 'google-cloud-storage'],
    'ibm': [
        'ibm-cloud-sdk-core', 'ibm-vpc', 'ibm-platform-services', 'ibm-cos-sdk'
    ],
    'docker': ['docker'],
    'lambda': [],
    'cloudflare': aws_dependencies,
    'scp': [],
    'oci': ['oci'],
    'kubernetes': ['kubernetes'],
}

extras_require['all'] = sum(extras_require.values(), [])

# Install aws requirements by default, as it is the most common cloud provider,
# and the installation is quick.
install_requires += extras_require['aws']

long_description = ''
readme_filepath = 'README.md'
# When sky/backends/wheel_utils.py builds wheels, it will not contain the
# README.  Skip the description for that case.
if os.path.exists(readme_filepath):
    long_description = io.open(readme_filepath, 'r', encoding='utf-8').read()
    long_description = parse_readme(long_description)

setuptools.setup(
    # NOTE: this affects the package.whl wheel name. When changing this (if
    # ever), you must grep for '.whl' and change all corresponding wheel paths
    # (templates/*.j2 and wheel_utils.py).
    name='skypilot',
    version=find_version('sky', '__init__.py'),
    packages=setuptools.find_packages(),
    author='SkyPilot Team',
    license='Apache 2.0',
    readme='README.md',
    description='SkyPilot: An intercloud broker for the clouds',
    long_description=long_description,
    long_description_content_type='text/markdown',
    setup_requires=['wheel'],
    requires_python='>=3.7',
    install_requires=install_requires,
    extras_require=extras_require,
    entry_points={
        'console_scripts': ['sky = sky.cli:cli'],
    },
    include_package_data=True,
    classifiers=[
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'License :: OSI Approved :: Apache Software License',
        'Operating System :: OS Independent',
        'Topic :: Software Development :: Libraries :: Python Modules',
        'Topic :: System :: Distributed Computing',
    ],
    project_urls={
        'Homepage': 'https://github.com/skypilot-org/skypilot',
        'Issues': 'https://github.com/skypilot-org/skypilot/issues',
        'Discussion': 'https://github.com/skypilot-org/skypilot/discussions',
        'Documentation': 'https://skypilot.readthedocs.io/en/latest/',
    },
)
