# Example: GitHub Actions + SkyPilot

![overview](https://i.imgur.com/2kg56i6.png "Overview")

![slack message](https://i.imgur.com/p50yoD5.png "Slack message")

This example provides a GitHub CI pipeline that automatically starts a SkyPilot job when a PR is merged to ``main`` branch and notifies a slack channel. It is useful for automatically trigger a training job when there is a new commit for config changes, and send notification for the training status and logs.

> **_NOTE:_**  This example is adapted from Metta AI's GitHub actions pipeline: https://github.com/Metta-AI/metta/tree/main

## Why use SkyPilot with GitHub Actions?

Pairing SkyPilot with GitHub Actions can automate routine experiments to run without manual input, improving iteration speed. Using SkyPilot with GitHub Actions can:

**Customize Workflow Triggers**: GitHub Actions provides a breadth of triggers to automate workflows, including:
- Code pushes to a branch
- Changes to specific files
- On a schedule

**Orchestrate and Monitor Jobs across Clouds**: SkyPilot allows the CI task to run across region, clouds and kubernetes clusters, and provides a single pane of glass to monitor the CI jobs.

**Enable Custom Notifications**: Send a notification whenever a CI job runs, with a link to monitor the job status and logs.


## Prerequisites

The following steps are required to use the example GitHub action in your repository.

### SkyPilot: Deploy a centralized API server

Follow the [instructions](https://docs.skypilot.co/en/latest/reference/api-server/api-server-admin-deploy.html) to deploy a centralized SkyPilot API server.

### [Optional] Obtain a SkyPilot service account key if using SSO

This section is required only for SkyPilot API Server deployment using OAuth.

![service accounts](https://i.imgur.com/PyUhBrf.png "Service accounts")

To create a service account key:

- **Navigate to the Users page**: On the main page of the SkyPilot dashboard, click "Users".
- **Access Service Account Settings**: Click "Service Accounts" located at the top of the page.
> **Note:** If "Service Accounts" section does not exist on the dashboard, the API server is not using SSO. This section can be skipped.
- **Create New Service Account**: Click "+ Create Service Account" button located at the top of the page and follow the instructions to create a service account token.

### GitHub: Define repository secrets

The example GitHub action relies on a few repository secrets. 
Follow this tutorial to add [repository secrets](https://docs.github.com/en/actions/security-for-github-actions/security-guides/using-secrets-in-github-actions#creating-secrets-for-a-repository).

In this example, create the following repository secrets:

- ``SKYPILOT_API_URL``: URL to the SkyPilot API server, in format of ``http(s)://url-or-ip``.
If using basic auth, the URL should also include the credentials in format of ``http(s)://username:password@url-or-ip``.
- ``SKYPILOT_SERVICE_ACCOUNT_TOKEN``: Only required if using OAuth. Service account token for GitHub actions user generated above.
- ``SLACK_BOT_TOKEN``: Optional, create a [Slack App](https://api.slack.com/apps) and get a slack "App-Level Token" with `connections:write` permission to send a summary message. If not provided, a slack message is not sent after a job is queued.
- ``SLACK_CHANNEL_ID``: Optional, Slack Channel ID to send a summary message. If not provided, a slack message is not sent after a job is queued.

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
└── tasks
    └── train.yaml
```

The `sky-job.yaml` defines the actual GitHub workflow. This GitHub action is configured to run in two modes:

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
  push:
    branches: [main]
```

The workflow checks out the GitHub repo to a specified commit, generates a unique job name, and launches a custom action located at ``.github/actions/launch-skypilot-job/action.yaml``.

The ``Launch SkyPilot Job`` action in turn uses a custom action located at ``.github/actions/setup-environment/action.yaml`` to install necessary dependencies (including ``skypilot``), and launches a SkyPilot job.

Once the job is successfully launched, ``sky-job.yaml`` then parses out the job ID of the submitted job.

The submitted job can be queried either by using `sky jobs queue` or by visiting the Jobs page of the SkyPilot API dashboard.
![dashboard page](https://i.imgur.com/JjDk30Z.png "Dashboard page")

A slack message is then sent to the configured slack channel. An example message is provided below:
![slack message](https://i.imgur.com/p50yoD5.png "Slack message")

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

### How do I limit / isolate the resources available to the workflow?

You can specify a specific cloud, region or kubernetes cluster for the workflow to use in the task YAML. Alternatively, you can define a separate [workspace](https://docs.skypilot.co/en/latest/admin/workspaces.html) the workflow can use, isolating the infrastructure the workflow has access to.
