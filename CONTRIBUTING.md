# Contributing to SkyPilot

Thank you for your interest in contributing to SkyPilot! We welcome and value 
all contributions to the project, including but not limited to: 

* [Bug reports](https://github.com/sky-proj/sky/issues) and [discussions](https://github.com/sky-proj/sky/discussions)
* [Pull requests](https://github.com/sky-proj/sky/pulls) for bug fixes and new features
* Test cases to make the codebase more robust
* Examples
* Documentation
* Tutorials, blog posts and talks on SkyPilot

## Contributing Code

We use Github to track issues and features. For new contributors, we recommend looking at issues labeled ["good first issue"](https://github.com/sky-proj/sky/issues?q=is%3Aopen+is%3Aissue+label%3A%22good+first+issue%22+).

### Installing SkyPilot for development
We recommend using editable mode (`-e`) when installing:
```bash
# SkyPilot requires python >= 3.6 and < 3.10.
# You can just install the dependencies for
# certain clouds, e.g., ".[aws,azure,gcp]"
pip install -e ".[all]"
pip install -r requirements-dev.txt
```
IMPORTANT: Please `export SKYPILOT_DEV=1` before running the CLI commands in the terminal, so that developers' usage logs do not pollute the actual user logs.

### Environment variables for developers
- `export SKYPILOT_DEV=1` to send usage logs to dev space.
- `export SKYPILOT_DISABLE_USAGE_COLLECTION=1` to disable usage logging.
- `export SKYPILOT_DEBUG=1` to show debugging logs (use logging.DEBUG level).
- `export SKYPILOT_MINIMIZE_LOGGING=1` to minimize the logging for demo purpose.

### Testing
To run smoke tests:
```
bash tests/run_smoke_tests.sh

# Run one of the smoke tests
bash tests/run_smoke_tests.sh test_minimal
```

For profiling code, use:
```
pip install tuna # Tuna is used for visualization of profiling data.
python3 -m cProfile -o sky.prof -m sky.cli status # Or some other command
tuna sky.prof
```

### Submitting pull requests
- Fork the SkyPilot repository and create a new branch for your changes.
- If relevant, add tests for your changes. For changes that touch the core system, run the [smoke tests](#testing) and ensure they pass.
- Follow the [Google style guide](https://google.github.io/styleguide/pyguide.html).
- After you commit, format your code with [`format.sh`](./format.sh).
- Push your changes to your fork and open a pull request in the SkyPilot repository.
- In the PR description, write a `Tested:` section to describe relevant tests performed.

### Dump timeline

Timeline is useful for performance analysis and debugging in SkyPilot.

Here are the APIs:

```python
from utils import timeline


# record a function in the timeline with the function path name
@timeline.event
def f(): ...


# record a function in the timeline using name='my_name'
@timeline.event(name='event_name')
def f(): ...


# record an event over a code block in the timeline:
with timeline.Event(name='event_name'):
  ...

# use a file lock with event:
with timeline.FileLockEvent(lockpath):
  pass
```

To dump the timeline, set environment variable `SKYPILOT_TIMELINE_FILE_PATH` to a file path.

View the dumped timeline file using `Chrome` (chrome://tracing) or [Perfetto](https://ui.perfetto.dev/).


### Some general engineering practice suggestions

These are suggestions, not strict rules to follow. When in doubt, follow the [style guide](https://google.github.io/styleguide/pyguide.html).

* Use `TODO(author_name)`/`FIXME(author_name)` instead of blank `TODO/FIXME`. This is critical for tracking down issues. You can write TODOs with your name and assign it to others (on github) if it is someone else's issue.
* Delete your branch after merging it. This keeps the repo clean and faster to sync.
* Use an exception if this is an error. Only use `assert` for debugging or proof-checking purposes. This is because exception messages usually contain more information.
* Use modern python features and styles that increases code quality.
  * Use f-string instead of `.format()` for short expressions to increase readability.
  * Use `class MyClass:` instead of `class MyClass(object):`. The later one was a workaround for python2.x.
  * Use `abc` module for abstract classes to ensure all abstract methods are implemented.
  * Use python typing. But you should not import external objects just for typing. Instead, import typing-only external objects under `if typing.TYPE_CHECKING:`.

### Updating the SkyPilot docker image

We maintain a docker image for users to easily run SkyPilot without requiring any installation. This image is manually updated with the following steps:

1. Authenticate with SkyPilot ECR repository. Contact `romil.bhardwaj@berkeley.edu` for access:
   ```
   aws ecr-public get-login-password --region us-east-1 | docker login --username AWS --password-stdin public.ecr.aws/a9w6z7w5
   ```

2. Build and tag the docker image:
   ```
   docker build -t public.ecr.aws/a9w6z7w5/sky:latest .
   ```

3. Push the image to ECR:
   ```
   docker push public.ecr.aws/a9w6z7w5/sky:latest
   ```
