"""Sky is a tool to run any workload seamlessly across different
cloud providers through a unified interface. No knowledge of cloud
offerings is required or expected – you simply define the workload
and its resource requirements, and Sky will automatically execute it on AWS,
Google Cloud Platform or Microsoft Azure."""

import os
import platform
import warnings

import setuptools

ROOT_DIR = os.path.dirname(__file__)

system = platform.system()
if system == 'Darwin':
    mac_version = platform.mac_ver()[0]
    mac_major, mac_minor = mac_version.split('.')[:2]
    mac_major = int(mac_major)
    mac_minor = int(mac_minor)
    if mac_major < 10 or (mac_major == 10 and mac_minor >= 15):
        warnings.warn(
            f"\'Detected MacOS version {mac_version}. MacOS version >=10.15 "
            "is required to install ray>=1.9\'")

install_requires = [
    'wheel',
    'Click',
    'colorama',
    'cryptography',
    'jinja2',
    'jsonschema',
    'networkx',
    'oauth2client',
    'pandas',
    'pycryptodome==3.12.0',
    'pendulum',
    'PrettyTable',
    # Lower local ray version is not fully supported, due to the
    # autoscaler issues (also tracked in #537).
    'ray[default]>=1.9.0',
    'rich',
    'tabulate',
    'filelock',  # TODO(mraheja): Enforce >=3.6.0 when python version is >= 3.7
    # This is used by ray. The latest 1.44.0 will generate an error
    # `Fork support is only compatible with the epoll1 and poll
    # polling strategies`
    'grpcio<=1.43.0',
    # The latest 4.21.1 will break ray. Enforce < 4.0.0 until Ray releases the fix.
    # https://github.com/ray-project/ray/pull/25211
    'protobuf<4.0.0',
    'psutil',
    'pulp',
]

extras_require = {
    'aws': ['awscli', 'boto3'],
    # TODO(zongheng): azure-cli is huge and takes a long time to install.
    # Tracked in: https://github.com/Azure/azure-cli/issues/7387
    'azure': ['azure-cli==2.30.0'],
    'gcp': ['google-api-python-client', 'google-cloud-storage'],
    'docker': ['docker'],
}

extras_require['all'] = sum(extras_require.values(), [])

setuptools.setup(
    name='sky',
    version='0.1.dev0',
    packages=setuptools.find_packages(),
    setup_requires=['wheel'],
    install_requires=install_requires,
    extras_require=extras_require,
    entry_points={
        'console_scripts': ['sky = sky.cli:cli'],
    },
    include_package_data=True,
    classifiers=[
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
    ],
    description='Sky Prototype',
    long_description=__doc__.replace('\n', ' '),
)
