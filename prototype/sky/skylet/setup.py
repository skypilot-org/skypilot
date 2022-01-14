import os
from setuptools import setup

ROOT_DIR = os.path.dirname(__file__)

install_requires = [
    'pendulum',
    'PrettyTable',
]

setup(
    name='skylet',
    version='0.1.dev0',
    packages=['skylet'],
    install_requires=install_requires,
    include_package_data=True,
    classifiers=[
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
    ],
    description='Skylet Prototype',
    long_description=open(os.path.join(ROOT_DIR, 'README.md'),
                          'r',
                          encoding='utf-8').read(),
)
