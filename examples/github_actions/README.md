# Example: Github Actions + SkyPilot

This example provides a GitHub CI pipeline that automatically starts a SkyPilot job when a PR is merged to ``main`` branch and notifies a slack channel. It is useful for automatically trigger a training job when there is a new commit for config changes, and send notification for the training status and logs.

> **_NOTE:_**  This example is adapted from Metta AI's GitHub actions pipeline: https://github.com/Metta-AI/metta/tree/main


## Prerequisites

The following steps are required to use the example github action in your repository.

### SkyPilot: Deploy a centralized API server

Follow the [instructions](https://docs.skypilot.co/en/latest/reference/api-server/api-server-admin-deploy.html) to deploy a centralized SkyPilot API server.

### SkyPilot: Obtain a service account key

For SkyPilot API Server deployment using OAuth, a service account key is required for the github action.

To create a service account key:

- **Navigate to the Users page**: On the main page of the SkyPilot dashboard, click "Users".
- **Access Service Account Settings**: Click "Service Accounts" located at the top of the page.
- **Create New Service Account**: Click "+ Create Service Account" button located at the top of the page and follow the instructions to create a service account token.

### GitHub: Define repository secrets

The example github action relies on a few repository secrets. 
Follow this tutorial to add [repository secrets](https://docs.github.com/en/actions/security-for-github-actions/security-guides/using-secrets-in-github-actions#creating-secrets-for-a-repository).

In this example, create the following repository secrets:

- ``SKYPILOT_SERVICE_ACCOUNT_TOKEN``: Service account token for Github actions user generated above.
- ``SLACK_BOT_TOKEN``: Slack bot token to send a summary message.
- ``SLACK_CHANNEL_ID``: Slack Channel ID to send a summary message.

### Workflow: Fill in the API server URL

`sky-job.yaml` provided in this example has an `env` section with a placeholder ``SKYPILOT_API_URL`` variable: 
```
env:
  RUN_NAME_PREFIX: "gh-actions"
  SKYPILOT_API_URL: "YOUR_SKYPILOT_API_URL"
```

fill in the ``SKYPILOT_API_URL`` with your API server URL.

## Repository Structure

The example repository has the following directory tree:
```
.
├── .git
│   ...
├── .github
│   ├── actions
│   │   ├── launch-skypilot-job
│   │   │   └── action.yaml
│   │   └── setup-environment
│   │       └── action.yaml
│   └── workflows
│       └── sky-job.yaml
├── sample_task.yaml
└── launch_sky.py
```

The `sky-job.yaml` defines the actual github workflow. This github action is configured to run in two modes:

- `workflow_dispatch`: Triggered manually via the "Actions" page of the GitHub repo.
- `push`: Triggered when a commit is pushed to specific branches (in this example, ``main``).

```yaml
on:
  workflow_dispatch:
    inputs:
      task_yaml_path:
        description: "Path to the task YAML file"
        required: true
        type: string
      commit_to_run:
        description: "The full commit hash to run the job against (required for manual runs)."
        required: true
        type: string
      num_gpus:
        description: "Number of GPUs to request (e.g., 1, 4). If empty, defaults to SkyPilot task definition (usually 1)."
        required: false
        type: string
      num_nodes:
        description: "Number of Nodes to request (e.g., 1, 2). If empty, defaults to SkyPilot task definition (usually 1)."
        required: false
        type: string
  push:
    branches: [main]
```

The workflow checks out the GitHub repo to a specified commit, generates a unique job name, and launches a custom action located at ``.github/actions/launch-skypilot-job/action.yaml``.

The ``Launch SkyPilot Job`` action in turn uses a custom action located at ``.github/actions/setup-environment/action.yaml`` to install necessary dependencies (including ``skypilot``), and calls ``launch_sky.py`` to launch the actual job.

Once the job is successfully launched, ``sky-job.yaml`` then parses out the job ID of the submitted job. A slack message is then sent to the configured slack channel. An example message is provided below:
```
Skypilot job 10 triggered by: push
SkyPilot Job Name: gh-actions.pr19.017451b.20251111_235537
✅ SkyPilot Job ID: 10
📝 View logs: sky jobs logs 10
🔗 View job: https://YOUR_SKYPILOT_API_URL/dashboard/jobs/10
```

## Frequently Asked Questions

### What if my target branch is named something other than ``main`` (e.g. ``master``)?

You can modify `sky-jobs.yaml` to specify a different target branch:
```diff
on:
  ...
  push:
-   branches: [main]
+   branches: [master]
```

### What if my SkyPilot API server is using Basic Auth instead of OAuth?

If SkyPilot API server is using basic auth, there is no need for a service account token. In this case, ``Configure SkyPilot API server`` step of ``launch-skypilot-job/action.yaml`` should be changed to not pass in a token:

```diff
inputs:
  ...
- skypilot_service_account_token:
-   description: "SkyPilot service account token"
-   required: true

runs:
  steps:
    ...
    - name: Configure SkyPilot API server
      shell: bash
      run: |
-       sky api login -e ${{ inputs.skypilot_api_url }} --token ${{ inputs.skypilot_service_account_token }}
+       sky api login -e ${{ inputs.skypilot_api_url }}
```

``sky-job.yaml`` should be modified to 

```diff
jobs:
  launch-batch-job:
    steps:
      ...
      - name: Launch SkyPilot Job via Custom Action
        id: skylaunch
        uses: ./.github/actions/launch-skypilot-job
        with:
          task_yaml_path: ${{ github.event.inputs.task_yaml_path }}
          job_name: ${{ steps.generate_job_name.outputs.job_name }}
          skypilot_api_url: ${{ env.SKYPILOT_API_URL }}
-         skypilot_service_account_token: ${{ secrets.SKYPILOT_SERVICE_ACCOUNT_TOKEN }}
          commit_sha: ${{ github.event_name == 'workflow_dispatch' && github.event.inputs.commit_to_run || github.sha }}

```

The credential should be supplied via ``SKYPILOT_API_URL`` environment variable instead, using the format ``http(s)://username:password@url-or-ip``.

Since the URL now contains sensitive credentials, it is suggested to provide ``SKYPILOT_API_URL`` via GitHub Secret instead of via the ``env`` section.
