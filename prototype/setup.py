from setuptools import setup

setup(
    name='sky',
    version='0.1dev',
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
)
