# Job Startup & Demand

The chart provisions `skypilot-job-startup` in the existing Grafana instance when
`grafana.enabled` and `grafana.sidecar.dashboards.enabled` are true. It uses the
existing `prometheus` datasource and API server `/metrics` scrape. The collector
requires managed jobs consolidation mode and the server's resilient collector
wrapper; updating the dashboard alone does not install the metrics.

## Semantics

- The fixed rolling cohort is tasks first eligible to run in the last seven days,
  independent of the Grafana time picker. Pipeline and dependency-blocked tasks
  join only when their `eligible_at` timestamp is set. Retries do not reset it.
- Startup is eligibility (`eligible_at`) to first run (`start_at`). `controller`
  is eligibility to first controller claim (`submitted_at`); `launch` is claim
  to first run, including acquisition, provisioning, image pulls, setup and
  retries. This is not pure GPU scheduling delay. Phase percentiles do not add.
- Tasks cancelled or failed before first run are separate outcomes. Their total
  wait ends at the task's terminal timestamp. Cancellation after a run remains
  in the started cohort. Current waiting gauges include tasks older than seven
  days, and exclude recovery after a run.
- Missing timing fields and invalid chronology are reported by reason.
  Tasks without `eligible_at` are excluded until they become eligible; elapsed
  time comes from epoch-second state columns, independent of event retention.
- All metrics are gauges. For `sky_managed_job_wait_7d_seconds_bucket`, use
  `histogram_quantile` directly on aggregated cumulative buckets. Never apply
  `rate` or `increase`. Counts are tasks, not GPU counts or GPU hours.
- Dimensions are workspace, user hash, cloud, requested GPU, and persisted
  resource labels `team` / `project`. Missing attribution is `unknown`; resource
  alternatives can produce a comma-separated GPU label. Historical task names
  or logs are not used to infer project membership.
- Per-task series are limited to the global 20 longest known waits, before
  dashboard filters. Aggregates contain no task IDs, names or commands.

Snapshots refresh through the existing background collector wrapper. A failing
or hung query retains its original snapshot timestamp. Panels discard snapshots
older than three minutes and deduplicate API replicas before aggregation. A fresh
empty cohort can display zero; missing/stale collection cannot.

After deploying both server code and chart, verify the snapshot age is below three
minutes and compare sampled outliers with the task eligibility, claim and start timestamps.
Check a retried task, a cancelled-before-start task and a currently waiting task.
