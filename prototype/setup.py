"""Setup file for Sky."""
import os
import setuptools

ROOT_DIR = os.path.dirname(__file__)

install_requires = [
    'Click',
    'absl-py',
    'colorama',
    'jinja2',
    'networkx',
    'oauth2client',
    'pandas',
    'pycryptodome==3.12.0',
    'pendulum',
    'PrettyTable',
    'pytest',
    'ray[default]',
    'tabulate',
    'docker',
    'wheel',
]

extras_require = {
    'aws': ['awscli==1.22.17', 'boto3'],
    'azure': ['azure-cli'],
    'gcp': ['google-api-python-client', 'google-cloud-storage'],
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
