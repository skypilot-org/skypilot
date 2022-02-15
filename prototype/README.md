# Sky

![pytest](https://github.com/concretevitamin/sky-experiments/actions/workflows/pytest.yml/badge.svg)

## Setup

```bash
# Sky requires python version >= 3.6

# You can just install the dependencies for
# certain clouds, e.g., ".[aws,azure,gcp]"
pip install -e ".[all]"

python examples/resnet_app.py

# Or try other examples:
ls examples/
```

## Cloud account setup
Running these setup enables Sky to launch resources on different clouds.
This should be run on your laptop/development machine where you will use Sky to launch jobs.

**AWS**. Install boto (`pip install boto3`) and configure your AWS credentials using `aws configure`.

**GCP**. Run:
```
pip install google-api-python-client
# Install `gcloud`; see https://cloud.google.com/sdk/docs/quickstart
conda install -c conda-forge google-cloud-sdk

# Init.
gcloud init

# Run this if you don't have a credentials file.
# This will generate ~/.config/gcloud/application_default_credentials.json.
gcloud auth application-default login

# Export environment variable to .bashrc
echo GOOGLE_APPLICATION_CREDENTIALS=~/.config/gcloud/application_default_credentials.json >> ~/.bashrc
source ~/.bashrc
```

**Azure**. Install the Azure CLI (`pip install azure-cli==2.22.0`) then login using `az login`. Set the subscription to use from the command line (`az account set -s <subscription_id>`). Ray Autoscaler does not work with the latest version of `azure-cli` as of 1.9.1, hence the fixed Azure version.

## SSH Access
The system currently supports SSH access for launched VMs by modifying your local `~/.ssh/config`. For git credentials to forward seamlessly, users must start their SSH agent and add their GitHub SSH key to it:
```
eval "$(ssh-agent -s)"
ssh-add -K /path/to/key  # e.g. ~/.ssh/id_ed25519
```
For more information on GitHub authentication and keys, see their [setup tutorial](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/generating-a-new-ssh-key-and-adding-it-to-the-ssh-agent#adding-your-ssh-key-to-the-ssh-agent).

## Some general engineering practice suggestions

These are suggestions, not strict rules to follow. For general coding style, follow [google style guide](https://google.github.io/styleguide/pyguide.html).

* Use `TODO(author_name)`/`FIXME(author_name)` instead of blank `TODO/FIXME`. This is critical for tracking down issues. You can write TODOs with your name and assign it to others (on github) if it is someone else's issue.
* Delete your branch after merging it. This keeps the repo clean and faster to sync.
* Use an exception if this is an error. Only use `assert` for debugging or proof-checking purpose. This is because exception messages usually contain more information.
* Use modern python features and styles that increases code quality.
  * Use f-string instead of `.format()` for short expressions to increase readability.
  * Use `class MyClass:` instead of `class MyClass(object):`. The later one was a workaround for python2.x.
  * Use `abc` module for abstract classes to ensure all abstract methods are implemented.
  * Use python typing. But you should not import external objects just for typing. Instead, import typing-only external objects under `if typing.TYPE_CHECKING:`.
