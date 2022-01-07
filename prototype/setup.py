import os
from setuptools import setup

ROOT_DIR = os.path.dirname(__file__)

setup(
    name='sky',
    version='0.1.dev0',
    packages=['sky'],
    install_requires=[
        'Click',
    ],
    entry_points={
        'console_scripts': [
            'sky = sky.cli:cli',
        ],
    },
    include_package_data=True,
    classifiers=[
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
    ],
    description="Sky Prototype",
    long_description=open(os.path.join(ROOT_DIR, "README.md"),
                          "r",
                          encoding="utf-8").read(),
)
