# Sky

![pytest](https://github.com/sky-proj/sky/actions/workflows/pytest.yml/badge.svg)

Sky is a framework to run any workload seamlessly across different cloud providers through a unified interface. No knowledge of cloud offerings is required or expected – you simply define the workload and its resource requirements, and Sky will automatically execute it on AWS, Google Cloud Platform or Microsoft Azure.

<!-- TODO: We need a logo here -->
## Getting Started
Please refer to our [documentation](https://sky-proj-sky.readthedocs-hosted.com/en/latest/).
- [Installation](https://sky-proj-sky.readthedocs-hosted.com/en/latest/getting-started/installation.html)
- [Quickstart](https://sky-proj-sky.readthedocs-hosted.com/en/latest/getting-started/quickstart.html)
- [Sky CLI](https://sky-proj-sky.readthedocs-hosted.com/en/latest/reference/cli.html)

## Developer Guide
### Setup
Use editable mode (`-e`) when installing:
```bash
# Sky requires python >= 3.6 and < 3.10.
# You can just install the dependencies for
# certain clouds, e.g., ".[aws,azure,gcp]"
pip install -e ".[all]"
pip install -r requirements-dev.txt
```

### Submitting pull requests
- After you commit, format your code with [`format.sh`](./format.sh).
- In the PR description, write a `Tested:` section to describe relevant tests performed.
- For changes that touch the core system, run the [smoke tests](#testing) and ensure they pass.
- Follow the [Google style guide](https://google.github.io/styleguide/pyguide.html).

### Updating the sky docker image
1. Authenticate with sky ECR repository. Contact romil.bhardwaj@berkeley.edu for access:
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

### Some general engineering practice suggestions

These are suggestions, not strict rules to follow. When in doubt, follow the [style guide](https://google.github.io/styleguide/pyguide.html).

* Use `TODO(author_name)`/`FIXME(author_name)` instead of blank `TODO/FIXME`. This is critical for tracking down issues. You can write TODOs with your name and assign it to others (on github) if it is someone else's issue.
* Delete your branch after merging it. This keeps the repo clean and faster to sync.
* Use an exception if this is an error. Only use `assert` for debugging or proof-checking purpose. This is because exception messages usually contain more information.
* Use modern python features and styles that increases code quality.
  * Use f-string instead of `.format()` for short expressions to increase readability.
  * Use `class MyClass:` instead of `class MyClass(object):`. The later one was a workaround for python2.x.
  * Use `abc` module for abstract classes to ensure all abstract methods are implemented.
  * Use python typing. But you should not import external objects just for typing. Instead, import typing-only external objects under `if typing.TYPE_CHECKING:`.

### Testing
To run smoke tests:
```
bash tests/run_smoke_tests.sh

# Run one of the smoke tests
bash tests/run_smoke_tests.sh test_minimal
```

For profiling code, use:
```
pip install tuna # Tuna for viz
python3 -m cProfile -o sky.prof -m sky.cli status # Or some other command
tuna sky.prof
```
