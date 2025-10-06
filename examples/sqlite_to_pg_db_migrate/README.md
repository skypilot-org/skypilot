# Sky SQLite to Postgres DB Migration

If you have a running skypilot API server that you want to minimize downtime while you upgrade
from sqlite db to an external postgres db.

## Prerequisites

- uv
- skypilot api server running
- access to k8s context with skypilot api server
- postgres db ready, exposed to skypilot api deployment as `FUTURE_SKYPILOT_DB_CONNECTION_URI` env var

## Steps

### Schedule some downtime

Assuming the reason you want to migrate from sqlite to postgres and not just start fresh is you have many active users. You should let the users know that you will perform this migration.

For roughly ~100 user deployment this script took 2 minutes to dump the data, but I'd recommend an hour block to be safe.

### Run the migration

Things that the launch script does:
-   create snapshot of sky state pvc
-   create configmap with migration script
-   delete skypilot service temporarily to prevent new requests
-   change skypilot api deployment to run migration
    -   mounts migration script inside container
    -   backs up original args, replaces them with a script to run migration
-   perform db migration in deployment, outputs logs
-   change skypilot deployment to use external PG DB going forward
    -   disable other config by moving it to `~/.sky/config.yaml.bak`
-   change sky config configmap `config.yaml` to `config.yaml.bak`, make `config.yaml` just `{}`
-   recreate service
-   delete script configmap

The launch script should fail gracefully on error and restore the original config

To launcht the migration, set the env variables appropriately or pass the flags
```bash
# see available args with
uv run --script launch_db_migrate.py --help

# run migration
uv run --script launch_db_migrate.py --context skypilot-api-context --namespace skypilot
```
