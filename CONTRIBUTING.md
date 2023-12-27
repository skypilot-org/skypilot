# Contributing to SkyPilot

Thank you for your interest in contributing to SkyPilot! We welcome and value 
all contributions to the project, including but not limited to: 

* [Bug reports](https://github.com/skypilot-org/skypilot/issues) and [discussions](https://github.com/skypilot-org/skypilot/discussions)
* [Pull requests](https://github.com/skypilot-org/skypilot/pulls) for bug fixes and new features
* Test cases to make the codebase more robust
* Examples
* Documentation
* Tutorials, blog posts and talks on SkyPilot

## Contributing Code

We use GitHub to track issues and features. For new contributors, we recommend looking at issues labeled ["good first issue"](https://github.com/sky-proj/sky/issues?q=is%3Aopen+is%3Aissue+label%3A%22good+first+issue%22+).

### Installing SkyPilot for development
```bash
# SkyPilot requires python >= 3.7.
# You can just install the dependencies for
# certain clouds, e.g., ".[aws,azure,gcp,lambda]"
pip install -e ".[all]"
pip install -r requirements-dev.txt
```

### Testing
To run smoke tests (NOTE: Running all smoke tests launches ~20 clusters):
```
# Run all tests except for AWS and Lambda Cloud
pytest tests/test_smoke.py

# Terminate a test's cluster even if the test failed (default is to keep it around for debugging)
pytest tests/test_smoke.py --terminate-on-failure

# Re-run last failed tests
pytest --lf

# Run one of the smoke tests
pytest tests/test_smoke.py::test_minimal

# Only run managed spot tests
pytest tests/test_smoke.py --managed-spot

# Only run test for AWS + generic tests
pytest tests/test_smoke.py --aws

# Change cloud for generic tests to aws
pytest tests/test_smoke.py --generic-cloud aws
```

For profiling code, use:
```
pip install tuna # Tuna is used for visualization of profiling data.
python3 -m cProfile -o sky.prof -m sky.cli status # Or some other command
tuna sky.prof
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
* Common dependencies and some utilities (rsync, screen, vim, nano etc) are pre-installed
* requirements-dev.txt is pre-installed
* Environment variables for dev/debug are set correctly
* Automatically clones the latest master to `/sky_repo/skypilot` when the container is launched.
  * Note that you still have to manually run `pip install -e ".[all]"` to install skypilot, it is not pre-installed.
  * If your branch is on the SkyPilot repo, you can run `git checkout <your_branch>` to switch to your branch.

### Submitting pull requests
- Fork the SkyPilot repository and create a new branch for your changes.
- If relevant, add tests for your changes. For changes that touch the core system, run the [smoke tests](#testing) and ensure they pass.
- Follow the [Google style guide](https://google.github.io/styleguide/pyguide.html).
- Ensure code is properly formatted by running [`format.sh`](https://github.com/skypilot-org/skypilot/blob/master/format.sh).
- Push your changes to your fork and open a pull request in the SkyPilot repository.
- In the PR description, write a `Tested:` section to describe relevant tests performed.

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

### Environment variables for developers
- `export SKYPILOT_DISABLE_USAGE_COLLECTION=1` to disable usage logging.
- `export SKYPILOT_DEBUG=1` to show debugging logs (use logging.DEBUG level).
- `export SKYPILOT_MINIMIZE_LOGGING=1` to minimize logging. Useful when trying to avoid multiple lines of output, such as for demos.
