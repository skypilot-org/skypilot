# Example: Github CI + SkyPilot

Run a SkyPilot Task with Github Actions

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

Replace `<server>` on line 36 of `run_sky.yaml` with a valid API server endpoint.

When a PR is submitted against `main` branch of this repo or a commit is merged the `main` branch, `run_sky.yaml` launches `sample_job.yaml` using `skypilot_config.yaml` as config.
