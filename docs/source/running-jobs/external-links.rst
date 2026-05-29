.. _external-links:

External Links
==============

External links are URLs associated with managed jobs and clusters that are displayed in the SkyPilot dashboard. This is useful for linking to external dashboards, experiment trackers, or any other relevant resources.

SkyPilot automatically detects and displays three types of links:

1. **Instance links**: For jobs running on AWS, GCP, or Azure, SkyPilot automatically adds links to the cloud console for the underlying instance.
2. **Log-detected links (built-in)**: The dashboard automatically parses job logs to detect URLs from supported services (currently Weights & Biases) and displays them as external links.
3. **Admin-configured custom URLs**: Administrators can register a list of labeled regex patterns in the SkyPilot config. Matching URLs that appear in job logs (job detail page) or cluster provision logs (cluster detail page) are rendered as clickable, labeled links.

.. image:: ../images/examples/external-links/job-page-wandb.png
  :width: 800
  :alt: Managed jobs external links

Supported services
~~~~~~~~~~~~~~~~~~

SkyPilot automatically detects URLs from the following services in your job logs:

- **Weights & Biases (W&B)**: Run URLs on W&B SaaS (e.g., ``https://wandb.ai/<entity>/<project>/runs/<run_id>``) and W&B Dedicated Cloud tenants (e.g., ``https://<tenant>.wandb.io/<entity>/<project>/runs/<run_id>``)

When your job prints a URL from a supported service to stdout or stderr, the dashboard will automatically extract it and display it in the "External Links" section.

Example: Using Weights & Biases
-------------------------------

When using W&B for experiment tracking, the W&B library automatically prints the run URL to stdout when you initialize a run. SkyPilot detects this and displays it in the dashboard.

Here's an example training job:

.. code-block:: yaml

  # wandb_training.yaml
  name: wandb-training

  envs:
    WANDB_API_KEY: null # Set via --secret

  setup: |
    pip install wandb torch

  run: |
    python train.py

.. code-block:: python

  # train.py
  import wandb
  run = wandb.init(project='example', name='demo-run')
  run.log({'loss': 1.0})
  run.finish()

Launch the job:

.. code-block:: console

  $ sky jobs launch -n wandb-example-job --env WANDB_API_KEY=$WANDB_API_KEY wandb_training.yaml

Once the job starts and W&B prints the run URL to the logs, you'll see the link appear in the dashboard:

.. image:: ../images/examples/external-links/job-page-wandb.png
  :width: 800
  :alt: Job detail page showing W&B external link

Clicking the link will take you directly to the W&B run page allowing you to quickly view the run metrics and artifacts.

.. image:: ../images/examples/external-links/wandb.png
  :width: 800
  :alt: W&B run page


Admin-configured custom URLs
----------------------------

Administrators can extend the built-in W&B detection with their own
``{label, regex}`` entries in the SkyPilot server config. Any URL printed
to the logs that matches a configured regex is rendered as a clickable,
labeled link on both the cluster and job detail pages.

Add the entries under a top-level ``dashboard`` block in
``~/.sky/config.yaml`` on the SkyPilot API server:

.. code-block:: yaml

  dashboard:
    external_links:
      - label: "Grafana"
        regex: 'https://grafana\.internal\.example\.com/d/[a-z0-9]+.*'
      - label: "Internal tools"
        regex: 'https://tools\.internal\.example\.com/.*'

Each entry takes:

- ``label``: The text shown to users in the External Links section.
- ``regex``: A Python-style regex matched against whitespace-delimited
  tokens in streamed log output. Each pattern resolves to at most one URL
  per cluster or job (the first match wins).

After updating the config, restart the API server
(``sky api stop && sky api start``) so the new entries are loaded.

Custom URL matches appear on:

- The **job detail page** under External Links, alongside W&B and instance
  console links. Scanning happens as job logs stream into the browser.
- The **cluster detail page** under External Links. The dashboard
  automatically streams the tail of the most-recent job's logs to scan for
  matches; if you also expand the Provision Logs section, those lines are
  scanned as well.

Regexes that fail to compile are rejected at config load time with a clear
error, so a malformed entry does not silently disable other entries.
