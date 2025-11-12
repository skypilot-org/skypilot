"""Dependencies for SkyPilot.

This file is imported by setup.py, so:
- It may not be able to import other skypilot modules, since sys.path may not be
  correct.
- It should not import any dependencies, as they may not be installed yet.
"""
import sys
from typing import Dict, List

clouds_with_ray = ['ibm', 'docker', 'scp']

install_requires = [
    'wheel<0.46.0',  # https://github.com/skypilot-org/skypilot/issues/5153
    'setuptools',  # TODO: match version to pyproject.toml once #5153 is fixed
    'pip',
    'cachetools',
    # NOTE: ray requires click>=7.0.
    # click 8.2.0 has a bug in parsing the command line arguments:
    # https://github.com/pallets/click/issues/2894
    # TODO(aylei): remove this once the bug is fixed in click.
    'click >= 7.0, < 8.2.0',
    'colorama',
    'cryptography',
    # Jinja has a bug in older versions because of the lack of pinning
    # the version of the underlying markupsafe package. See:
    # https://github.com/pallets/jinja/issues/1585
    'jinja2 >= 3.0',
    'jsonschema',
    'networkx',
    'pandas>=1.3.0',
    'pendulum',
    # PrettyTable with version >=2.0.0 is required for the support of
    # `add_rows` method.
    'PrettyTable >= 2.0.0',
    'python-dotenv',
    'rich',
    'tabulate',
    # Light weight requirement, can be replaced with "typing" once
    # we deprecate Python 3.7 (this will take a while).
    'typing_extensions',
    # filelock 3.15.0 or higher is required for async file locking.
    'filelock >= 3.15.0',
    'packaging',
    'psutil',
    'pulp',
    # Cython 3.0 release breaks PyYAML 5.4.*
    # (https://github.com/yaml/pyyaml/issues/601)
    # <= 3.13 may encounter https://github.com/ultralytics/yolov5/issues/414
    'pyyaml > 3.13, != 5.4.*',
    'ijson',
    'orjson',
    'requests',
    # SkyPilot inherits from uvicorn.Server to customize the behavior of
    # uvicorn, so we need to pin uvicorn version to avoid potential break
    # changes.
    # Notes for current version check:
    # - uvicorn 0.33.0 is the latest version that supports Python 3.8
    # - uvicorn 0.36.0 removes setup_event_loop thus breaks SkyPilot's custom
    #   behavior.
    'uvicorn[standard] >=0.33.0, <0.36.0',
    'fastapi',
    # Some pydantic versions are not compatible with ray. Adopted from ray's
    # setup.py:
    # https://github.com/ray-project/ray/blob/ray-2.9.3/python/setup.py#L254
    # We need pydantic>=2.0.0 for API server and client.
    'pydantic!=2.0.*,!=2.1.*,!=2.2.*,!=2.3.*,!=2.4.*,<3,>2',
    # Required for Form data by pydantic
    'python-multipart',
    'aiofiles',
    'httpx',
    'setproctitle',
    'sqlalchemy',
    'psycopg2-binary',
    'aiosqlite',
    'asyncpg',
    # TODO(hailong): These three dependencies should be removed after we make
    # the client-side actually not importing them.
    'casbin',
    'sqlalchemy_adapter',
    # Required for API server metrics
    'prometheus_client>=0.8.0',
    'passlib',
    'bcrypt==4.0.1',
    'pyjwt',
    'gitpython',
    'types-paramiko',
    'alembic',
    'aiohttp',
    'anyio',
]

# See requirements-dev.txt for the version of grpc and protobuf
# used to generate the code during development.

# The grpc version at runtime has to be newer than the version
# used to generate the code.
GRPC = 'grpcio>=1.63.0'
# >= 5.26.1 because the runtime version can't be older than the version
# used to generate the code.
# < 7.0.0 because code generated for a major version V will be supported by
# protobuf runtimes of version V and V+1.
# https://protobuf.dev/support/cross-version-runtime-guarantee
PROTOBUF = 'protobuf>=5.26.1, < 7.0.0'

server_dependencies = [
    # TODO: Some of these dependencies are also specified in install_requires,
    # so they are redundant here. We should figure out if they are only needed
    # on the server (should remove from install_requires), or if they are needed
    # on the client (should remove from here).
    'casbin',
    'sqlalchemy_adapter',
    'passlib',
    'pyjwt',
    'aiohttp',
    'anyio',
    GRPC,
    PROTOBUF,
    'aiosqlite',
    'greenlet',
]

local_ray = [
    # Lower version of ray will cause dependency conflict for
    # click/grpcio/protobuf.
    # Ray 2.6.1+ resolved cluster launcher bugs
    # and grpcio issues on Apple Silicon.
    # https://github.com/ray-project/ray/releases/tag/ray-2.6.1
    'ray[default] >= 2.6.1',
]

remote = [
    GRPC,
    PROTOBUF,
]

# NOTE: Change the templates/jobs-controller.yaml.j2 file if any of the
# following packages dependencies are changed.
aws_dependencies = [
    # NOTE: this installs CLI V1. To use AWS SSO (e.g., `aws sso login`), users
    # should instead use CLI V2 which is not pip-installable. See
    # https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html.
    'awscli>=1.27.10',
    'botocore>=1.29.10',
    'boto3>=1.26.1',
    # NOTE: required by awscli. To avoid ray automatically installing
    # the latest version.
    'colorama < 0.4.5',
]

# Kubernetes 32.0.0 has an authentication bug:
# https://github.com/kubernetes-client/python/issues/2333
kubernetes_dependencies = [
    'kubernetes>=20.0.0,!=32.0.0',
    'websockets',
    'python-dateutil',
]

# azure-cli cannot be installed normally by uv, so we need to work around it in
# a few places.
AZURE_CLI = 'azure-cli>=2.65.0'

cloud_dependencies: Dict[str, List[str]] = {
    'aws': aws_dependencies,
    # TODO(zongheng): azure-cli is huge and takes a long time to install.
    # Tracked in: https://github.com/Azure/azure-cli/issues/7387
    # azure-identity is needed in node_provider.
    # We need azure-identity>=1.13.0 to enable the customization of the
    # timeout of AzureCliCredential.
    'azure': [
        AZURE_CLI,
        'azure-core>=1.31.0',
        'azure-identity>=1.19.0',
        'azure-mgmt-network>=27.0.0',
        'azure-mgmt-compute>=33.0.0',
        'azure-storage-blob>=12.23.1',
        'msgraph-sdk',
        'msrestazure',
    ],
    # We need google-api-python-client>=2.69.0 to enable 'discardLocalSsd'
    # parameter for stopping instances. Reference:
    # https://github.com/googleapis/google-api-python-client/commit/f6e9d3869ed605b06f7cbf2e8cf2db25108506e6
    'gcp': [
        'google-api-python-client>=2.69.0',
        'google-cloud-storage',
        # see https://github.com/conda/conda/issues/13619
        # see https://github.com/googleapis/google-api-python-client/issues/2554
        'pyopenssl >= 23.2.0, <24.3.0',
    ],
    'ibm': [
        'ibm-cloud-sdk-core',
        'ibm-vpc',
        'ibm-platform-services>=0.48.0',
        'ibm-cos-sdk',
    ] + local_ray,
    'docker': ['docker'] + local_ray,
    'lambda': [],  # No dependencies needed for lambda
    'cloudflare': aws_dependencies,
    'coreweave': aws_dependencies + kubernetes_dependencies,
    'scp': local_ray,
    'oci': ['oci'],
    'kubernetes': kubernetes_dependencies,
    'ssh': kubernetes_dependencies,
    # For the container registry auth api. Reference:
    # https://github.com/runpod/runpod-python/releases/tag/1.6.1
    # RunPod needs a TOML parser to read ~/.runpod/config.toml. On Python 3.11+
    # stdlib provides tomllib; on lower versions we depend on tomli explicitly.
    'runpod': ['runpod>=1.6.1', 'tomli; python_version < "3.11"'],
    'fluidstack': [],  # No dependencies needed for fluidstack
    'cudo': ['cudo-compute>=0.1.10'],
    'paperspace': [],  # No dependencies needed for paperspace
    'primeintellect': [],  # No dependencies needed for primeintellect
    'do': ['pydo>=0.3.0', 'azure-core>=1.24.0', 'azure-common'],
    'vast': ['vastai-sdk>=0.1.12'],
    'vsphere': [
        'pyvmomi==8.0.1.0.2',
        # vsphere-automation-sdk is also required, but it does not have
        # pypi release, which cause failure of our pypi release.
        # https://peps.python.org/pep-0440/#direct-references
        # We have the instruction for its installation in our
        # docs instead.
        # 'vsphere-automation-sdk @ git+https://github.com/vmware/vsphere-automation-sdk-python.git@v8.0.1.0' pylint: disable=line-too-long
    ],
    'nebius': [
        # Nebius requires grpcio and protobuf, so we need to include
        # our constraints here.
        'nebius>=0.2.47',
        GRPC,
        PROTOBUF,
    ] + aws_dependencies,
    'hyperbolic': [],  # No dependencies needed for hyperbolic
    'seeweb': ['ecsapi==0.4.0'],
    'shadeform': [],  # No dependencies needed for shadeform
}

# Calculate which clouds should be included in the [all] installation.
clouds_for_all = set(cloud_dependencies)

if sys.version_info < (3, 10):
    # Nebius needs python3.10. If python 3.9 [all] will not install nebius
    clouds_for_all.remove('nebius')
    clouds_for_all.remove('seeweb')

if sys.version_info >= (3, 12):
    # The version of ray we use does not work with >= 3.12, so avoid clouds
    # that require ray.
    clouds_for_all -= set(clouds_with_ray)
    # vast requires setuptools==51.1.1 which will not work with python >= 3.12
    # TODO: Remove once https://github.com/vast-ai/vast-sdk/pull/6 is released
    clouds_for_all.remove('vast')

cloud_extras = {
    cloud: dependencies + server_dependencies
    for cloud, dependencies in cloud_dependencies.items()
}

extras_require: Dict[str, List[str]] = {
    # Include server_dependencies with each cloud.
    **cloud_extras,
    'all': list(set().union(*[cloud_extras[cloud] for cloud in clouds_for_all])
               ),
    'remote': remote,
    'server': server_dependencies,
}
