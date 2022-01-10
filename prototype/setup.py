import os
from setuptools import setup

ROOT_DIR = os.path.dirname(__file__)

install_requires = [
    'Click',
    'absl-py',
    'boto3',
    'colorama',
    'jinja2',
    'networkx',
    'oauth2client',
    'pandas',
    'pycryptodome==3.4.3',
    'pendulum',
    'PrettyTable',
    'pytest',
    'ray[default]',
    'tabulate',
    'docker',
]

extras_require = {
    'aws': ['awscli==1.22.17'],
    # ray <= 1.9.1 requires an older version of azure-cli. We can get rid of
    # this version requirement once ray 1.10 is released.
    'azure': ['azure-cli==2.22.0'],
    'gcp': ['google-api-python-client', 'google-cloud-storage'],
}

extras_require['all'] = sum(extras_require.values(), [])

setup(
    name='sky',
    version='0.1.dev0',
    packages=['sky'],
    install_requires=install_requires,
    extras_require=extras_require,
    entry_points={
        'console_scripts': [
            'sky = sky.cli:cli',
        ],
    },
    include_package_data=True,
    classifiers=[
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
    ],
    description='Sky Prototype',
    long_description=open(os.path.join(ROOT_DIR, 'README.md'),
                          'r',
                          encoding='utf-8').read(),
)
