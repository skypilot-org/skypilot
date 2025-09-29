# Contributing to SkyPilot

Thank you for your interest in contributing to SkyPilot! We welcome and value
all contributions to the project, including but not limited to:

- [Bug reports](https://github.com/skypilot-org/skypilot/issues) and [discussions](https://github.com/skypilot-org/skypilot/discussions)
- [Pull requests](https://github.com/skypilot-org/skypilot/pulls) for bug fixes and new features
- Test cases to make the codebase more robust
- Examples
- Documentation
- Tutorials, blog posts and talks on SkyPilot

## Contributing code

We use GitHub to track issues and features. For new contributors, we recommend looking at issues labeled ["good first issue"](https://github.com/sky-proj/sky/issues?q=is%3Aopen+is%3Aissue+label%3A%22good+first+issue%22+).

### Installing SkyPilot for development

Follow the steps below to set up a local development environment for contributing to SkyPilot.

#### Create a conda environment
To avoid package conflicts, create and activate a clean conda environment:
```bash
# SkyPilot requires 3.7 <= python <= 3.11.
conda create -y -n sky python=3.10
conda activate sky
```

#### Install SkyPilot
To install SkyPilot, please fork [skypilot-org/skypilot](https://github.com/skypilot-org/skypilot) to your GitHub account and run:
```bash
# Clone your forked repo
git clone https://github.com/<your-github-username>/skypilot.git

# Set upstream to keep in sync with the official repo
cd skypilot
git remote add upstream https://github.com/skypilot-org/skypilot.git

# Install SkyPilot in editable mode
pip install -e ".[all]"
# Alternatively, install specific cloud support only:
# pip install -e ".[aws,azure,gcp,lambda]"

# Install development dependencies
pip install -r requirements-dev.txt
```

#### (Optional) Install `pre-commit`
You can also install `pre-commit` hooks to help automatically format your code on commit:
```bash
pip install pre-commit
pre-commit install
```

### Generating Python files from protobuf
Whenever any protobuf file is changed (in `sky/schemas/proto`), run this to regenerate the Python files:
```bash
python -m grpc_tools.protoc \
        --proto_path=sky/schemas/generated=sky/schemas/proto \
        --python_out=. \
        --grpc_python_out=. \
        --pyi_out=. \
        sky/schemas/proto/*.proto
```

### Testing

To run smoke tests (NOTE: Running all smoke tests launches ~20 clusters):

```
# Run all tests on AWS and Azure (default smoke test clouds)
pytest tests/test_smoke.py

# Terminate a test's cluster even if the test failed (default is to keep it around for debugging)
pytest tests/test_smoke.py --terminate-on-failure

# Re-run last failed tests
pytest --lf

# Run one of the smoke tests
pytest tests/test_smoke.py::test_minimal

# Only run managed spot tests
pytest tests/test_smoke.py --managed-spot

# Only run test for GCP + generic tests
pytest tests/test_smoke.py --gcp

# Change cloud for generic tests to Azure
pytest tests/test_smoke.py --generic-cloud azure
```

For profiling code, use:

```
pip install py-spy # py-spy is a sampling profiler for Python programs
py-spy record -t -o sky.svg -- python -m sky.cli status # Or some other command
py-spy top -- python -m sky.cli status # Get a live top view
py-spy -h # For more options
```

#### Testing in a container

It is often useful to test your changes in a clean environment set up from scratch. Using a container is a good way to do this.
We have a dev container image `berkeleyskypilot/skypilot-debug` which we use for debugging skypilot inside a container. Use this image by running:

```bash
docker run -it --rm --name skypilot-debug berkeleyskypilot/skypilot-debug /bin/bash
# On Apple silicon Macs:
docker run --platform linux/amd64 -it --rm --name skypilot-debug berkeleyskypilot/skypilot-debug /bin/bash
```

It has some convenience features which you might find helpful (see [Dockerfile](https://github.com/skypilot-org/skypilot/blob/dev/dockerfile_debug/Dockerfile_debug)):

- Common dependencies and some utilities (rsync, screen, vim, nano etc) are pre-installed
- requirements-dev.txt is pre-installed
- Environment variables for dev/debug are set correctly
- Automatically clones the latest master to `/sky_repo/skypilot` when the container is launched.
  - Note that you still have to manually run `pip install -e ".[all]"` to install skypilot, it is not pre-installed.
  - If your branch is on the SkyPilot repo, you can run `git checkout <your_branch>` to switch to your branch.

### Submitting pull requests

- Fork the SkyPilot repository and create a new branch for your changes.
- If relevant, add tests for your changes. For changes that touch the core system, run the [smoke tests](#testing) and ensure they pass.
- Follow the [Google style guide](https://google.github.io/styleguide/pyguide.html).
- Ensure code is properly formatted by running [`format.sh`](https://github.com/skypilot-org/skypilot/blob/master/format.sh).
  - [Optional] You can also install pre-commit hooks by running `pre-commit install` to automatically format your code on commit.
- Push your changes to your fork and open a pull request in the SkyPilot repository.
- In the PR description, write a `Tested:` section to describe relevant tests performed.

### Some general engineering practice suggestions

These are suggestions, not strict rules to follow. When in doubt, follow the [style guide](https://google.github.io/styleguide/pyguide.html).

- Use `TODO(author_name)`/`FIXME(author_name)` instead of blank `TODO/FIXME`. This is critical for tracking down issues. You can write TODOs with your name and assign it to others (on github) if it is someone else's issue.
- Delete your branch after merging it. This keeps the repo clean and faster to sync.
- Use an exception if this is an error. Only use `assert` for debugging or proof-checking purposes. This is because exception messages usually contain more information.
- Use [`LazyImport`](https://github.com/skypilot-org/skypilot/blob/master/sky/adaptors/common.py) for third-party modules that meet these criteria:
  - The module is imported during `import sky`
  - The module has a significant import time (e.g. > 100ms)
- To measure import time:
  - Basic check: `python -X importtime -c "import sky"`
  - Detailed visualization: use [`tuna`](https://github.com/nschloe/tuna) to analyze import time:
    ```bash
    python -X importtime -c "import sky" 2> import.log
    tuna import.log
    ```
- Use modern python features and styles that increases code quality.
  - Use f-string instead of `.format()` for short expressions to increase readability.
  - Use `class MyClass:` instead of `class MyClass(object):`. The later one was a workaround for python2.x.
  - Use `abc` module for abstract classes to ensure all abstract methods are implemented.
  - Use python typing. But you should not import external objects just for typing. Instead, import typing-only external objects under `if typing.TYPE_CHECKING:`.

### Environment variables for developers

- `export SKYPILOT_DISABLE_USAGE_COLLECTION=1` to disable usage logging.
- `export SKYPILOT_DEBUG=1` to show debugging logs (use logging.DEBUG level).
- `export SKYPILOT_MINIMIZE_LOGGING=1` to minimize logging. Useful when trying to avoid multiple lines of output, such as for demos.

### Test API server on Helm chart deployment

By default, the [Helm Chart Deployment](https://docs.skypilot.co/en/latest/reference/api-server/api-server-admin-deploy.html) will use the latest released API Server. To test the local change on API Server, you can follow the steps below.

First, prepare and build the local changes:

```bash
# Ensure the helm repository is added and up to date
helm repo add skypilot https://helm.skypilot.co
helm repo update

# Build the local changes
helm dependency build ./charts/skypilot

# Build the local SkyPilot changes
DOCKER_IMAGE=my-docker-repo/image-name:v1 # change the tag to deploy the new changes
docker buildx build --push --platform linux/amd64  -t $DOCKER_IMAGE -f Dockerfile .
```

Then start the API Server with new changes:

```bash
# The following variables will be used throughout the guide
# NAMESPACE is the namespace to deploy the API server in
NAMESPACE=skypilot
# RELEASE_NAME is the name of the helm release, must be unique within the namespace
RELEASE_NAME=skypilot
# Set up basic username/password HTTP auth, or use OAuth2 proxy
WEB_USERNAME=sk
WEB_PASSWORD=pw
AUTH_STRING=$(htpasswd -nb $WEB_USERNAME $WEB_PASSWORD)
# Deploy the API server
helm upgrade --install $RELEASE_NAME ./charts/skypilot --devel \
  --namespace $NAMESPACE \
  --create-namespace \
  --set ingress.authCredentials=$AUTH_STRING \
  --set apiService.image=$DOCKER_IMAGE
```

> Notice that the tag should change every time you build the local changes.

Then, watch the status until the `READY` shows `1/1` and `STATUS` shows `Running`:

```bash
$ kubectl get pods --namespace $NAMESPACE -l app=${RELEASE_NAME}-api --watch
NAME                                  READY   STATUS              RESTARTS   AGE
skypilot-api-server-866b9b64c-ckxfm   0/1     ContainerCreating   0          25s
skypilot-api-server-866b9b64c-ckxfm   0/1     Running             0          44s
skypilot-api-server-866b9b64c-ckxfm   1/1     Running             0          75s
```

Finally, fetch the updated API Server endpoint:

```bash
HOST=$(kubectl get svc ${RELEASE_NAME}-ingress-nginx-controller --namespace $NAMESPACE -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
ENDPOINT=http://${WEB_USERNAME}:${WEB_PASSWORD}@${HOST}
echo $ENDPOINT
```

### Backward compatibility guidelines

SkyPilot adopts a client-server achitecture and maintains backward compatibility between client and server to ensure smooth upgrades for users. Starting from `0.10.0`, SkyPilot guarantees compatibility between adjacent minor versions. That is:

- Changes should always be added in a backward compatible manner, otherwise the compatibility for the previous minor version will be broken.
- It is an opportunity to remove legacy compatibility code when a new minor version is released, e.g. when we release `0.12.0`, we can [remove the legacy compatibility code](#removing-compatibility-code) for `0.10.0` in our codebase.

The general guideline to keep backward compatibility is to bump the `API_VERSION` constant in [`sky/server/constants.py`](https://github.com/skypilot-org/skypilot/blob/master/sky/server/constants.py) when introducing an API change and handle backward compatibility based on the `API_VERSION` of the remote peer:

```diff
# sky/server/constants.py
- API_VERSION = 11
+ API_VERSION = 12

# Application code
+ from sky.server import versions
+ # Handle the case where the remote peer runs in an API version older than 12
+ if versions.get_remote_api_version() < 12:
+   ...
```

Some concrete examples are listed below.

#### Adding new APIs

Bump the `API_VERSION` when adding a new API. Then:

- For new SDK methods that calls the new API, add the `@versions.minimal_api_version(API_VERSION)` decorator to the method:

    ```python
    from sky.server import versions

    # check_server_healthy_or_start is necessary to check and get server's API version,
    # this decorator is typically added to all the SDK methods and will be omitted in the
    # following examples.
    @server_common.check_server_healthy_or_start
    @versions.minimal_api_version(12)
    def new_feature_method():
        """This method requires server API version 12 or higher."""
        pass
    ```

- For existing SDK methods that will be modified to call the new API, the business logic should handle backward compatibility based on the server's API version:

    ```python
    from sky.server import versions

    def existing_sdk_method():
        if versions.get_remote_api_version() >= 12:
            # Call the new API
        else:
            # Proceed without the new API. Usually we just keep the same behavior
            # as before the new API is introduced.
    ```

#### Adding new fields to API payload

By convention, we define API payloads in `sky/server/api/payloads.py` and there are test cases to enforce the newly added fields must have a default value to keep backward compatibility at API level:

- When receiving a payload from an older version without the new field, the default value is used for the missing new field.
- When receiving a payload from a newer version with a new field, the value of the new field is ignored.

However, when the value of the new field is taken from an user input (e.g. CLI flag), we should add a warning message to inform the user that the new field is ignored. An API version bump is required in this case. For example:

```python
from sky.server import versions

@click.option('--newflag', default=None)
def cli_entry_point(newflag: Optional[str] = None):
    # The new flag is set but the server does not support the new field yet
    if newflag is not None and versions.get_remote_api_version() < 12:
        logger.warning('The new flag is ignored because the server does not support it yet.')
```

We should also be careful when adding new fields that are not directly visible in
`sky/server/api/payloads.py`, but is also being sent from the client to the server. This
is mainly for validating objects from the client.
As an example, this request body contains a single field, the string representation of a DAG.

```
class ValidateBody(DagRequestBodyWithRequestOptions):
    """The request body for the validate endpoint."""
    dag: str
```

A DAG consists of Tasks, so when adding new fields to Task, we should also handle backwards
compatibility in the serialization, otherwise the server may not recognize the new field
from the client and return an error during validation.


#### Adding new fields to API response body

##### Adding new fields to the existing objects in the API response body

When adding new fields to the existing objects that are serialized in API response bodies (such as resource handles), special care must be taken to ensure older clients can deserialize objects from newer servers. This commonly occurs with objects that are pickled and sent over the API.

For example, if you add a new field like `SSHTunnelInfo` to `CloudVmRayResourceHandle`, older clients without this class definition will fail during deserialization with errors like:
```
AttributeError: Can't get attribute 'SSHTunnelInfo' on <module 'sky.backends.cloud_vm_ray_backend'>
```

To handle this:

1. **Server-side encoding**: Modify the relevant encoders in `sky/server/requests/serializers/encoders.py` to
remove or clean problematic fields before serialization when serving older clients.

2. **Exception handling**: Update `sky/exceptions.py` if exceptions containing these objects also need
backwards compatibility processing.

See the `prepare_handle_for_backwards_compatibility` function and its usage for a concrete example of this.

##### Adding new fields to the API response body

When you need to add a brand-new field to the response body or change the response type (e.g., from `List` to `Dict`), prefer introducing a new API instead. See [Refactoring existing APIs](#refactoring-existing-apis) for details.

#### Refactoring existing APIs

Refactoring existing APIs can be tricky. It is recommended to add a new API instead. Then the compatibility issue can be addressed in the same way as [Adding new APIs](#adding-new-apis), e.g.:

- `constants.py`:

    ```diff
    - API_VERSION = 11
    + API_VERSION = 12
    ```

- `server.py`:

    ```python
    @app.post('/api')
    async def api():
        ...

    @app.post('/api_v2')
    async def api_v2():
        ...
    ```

- `sdk.py`:

    ```python
    from sky.server import versions

    def sdk_method():
        if versions.get_remote_api_version() >= 12:
            # call /api_v2
        else:
            # call /api
    ```

If the refactoring is happen to be simple (e.g. just change the response payload structure), we can also directly modify the existing API. For example:

- `server.py`:

    ```python
    from sky.server import versions

    @app.post('/api')
    async def api() -> Union[Response, ResponseV1]:
        if versions.get_remote_api_version() >= 12:
            return ResponseV2()
        else:
            return Response()
    ```

#### Removing compatibility code

To reduce the maintenance burden, the SkyPilot CI pipeline automatically updates the `MIN_COMPATIBLE_API_VERSION` field in `sky/server/constants.py` when a new minor version is released.
The SkyPilot CLI/SDK or API server will raise an error if the remote peer runs in an API version lower than `MIN_COMPATIBLE_API_VERSION`.
Therefore, compatible code below this version can be safely removed in our codebase.

For example, if we have added the example changes metioned in [Refactoring existing APIs](#refactoring-existing-apis) and now the `MIN_COMPATIBLE_API_VERSION` is bumped to 13 by the CI pipeline (which means we can drop compatibility for API version 12), then the following codes can be removed:

```diff
# sdk.py
def sdk_method():
- if versions.get_remote_api_version() >= 12:
-     # call /api_v2
- else:
-     # call /api
+ # call /api_v2

# server.py
- @app.post('/api')
- async def api():
-    ...
```
