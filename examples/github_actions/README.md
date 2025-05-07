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

`run_sky.yaml` launches `sample_job.yaml` using `skypilot_config.yaml` as config.
