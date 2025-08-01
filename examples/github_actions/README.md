# Example: Github Actions + SkyPilot

Run a SkyPilot Task with Github Actions

## Define a repository secret

You may need to inject sensitive variables (such as authentication credentials, etc.) into the github action.
Follow ths github tutorial to add a [repository secret](https://docs.github.com/en/actions/security-for-github-actions/security-guides/using-secrets-in-github-actions#creating-secrets-for-a-repository).

In this example, create a repository secret named `SKYPILOT_API_SERVER_ENDPOINT` that stores the remote API server endpoint.

## Define a workflow YAML

Given a simple repository with following directory tree:
```
.
├── .git
│   ...
├── .github
│   └── workflows
│       └── run-sky.yaml
├── sample_job.yaml
└── skypilot_config.yaml
```

When a PR is submitted against `main` branch of this repo or a commit is merged the `main` branch, `run_sky.yaml` launches `sample_job.yaml` using `skypilot_config.yaml` as config.
