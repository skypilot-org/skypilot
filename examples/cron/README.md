# Example: Cron + SkyPilot

Run a SkyPilot Task with Cron

## Find the `sky` executable path

By default, `cron` runs with a highly restrictive `PATH` - `/bin:/usr/bin`.
It is recommended to specify any executable using its full path. To find the full path of `sky`, run

```console
which sky
```

A possible value returned by this command is `/opt/anaconda3/envs/sky/bin/sky`, which is used to denote the full `sky` path in the rest of this document.

## Define a simple cron job

Run `crontab -e`. This command opens an editable file.

This file can be edited to define cron jobs. A sample cron job that can be defined is:
```console
*/5 * * * * /opt/anaconda3/envs/sky/bin/sky status
```

The `*/5 * * * *` segment defines the schedule at which this cron job runs, in this specific case every 5 minutes.

To create a suitable schedule for a cron job, visit https://crontab.guru/.

## Log the job output

The output of the cron job is found in different places depending on the operating system. To find the job output easily, store the cronjob output to a file.

```console
sudo mkdir -p /absolute/path/to/logs/
crontab -e
```
then edit the crontab file to include:
```console
*/5 * * * * /opt/anaconda3/envs/sky/bin/sky status > "/absolute/path/to/logs/$(date +\%d\%m\%y_\%H\%M\%S).log" 2>&1
```

> `date` command can be specified here without using its full path, as
> this command is usually found at `/bin/date` which is part of a cronjob's PATH.

## Pass in a config file
To pass in a configuration file to the cronjob, specify a `--config` file with path to a config file.

crontab example:
```console
*/5 * * * * /opt/anaconda3/envs/sky/bin/sky status --config /absolute/path/to/config/file.yaml > "/absolute/path/to/logs/$(date +\%d\%m\%y_\%H\%M\%S).log" 2>&1
```

Alternatively, specify `SKYPILOT_GLOBAL_CONFIG` environment variable for the script.

crontab example:
```console
*/5 * * * * SKYPILOT_GLOBAL_CONFIG=/absolute/path/to/config/file.yaml /opt/anaconda3/envs/sky/bin/sky status > "/absolute/path/to/logs/$(date +\%d\%m\%y_\%H\%M\%S).log" 2>&1
```

## Run a task
To run a task, refer to this sample cron job:
```console
*/5 * * * * SKYPILOT_GLOBAL_CONFIG=/absolute/path/to/config/file.yaml /opt/anaconda3/envs/sky/bin/sky jobs launch /absolute/path/to/job.yaml -y > "/absolute/path/to/logs/$(date +\%d\%m\%y_\%H\%M\%S).log" 2>&1
```

## Run a task asynchronously
Simply add the `--async` flag:
```console
*/5 * * * * SKYPILOT_GLOBAL_CONFIG=/absolute/path/to/config/file.yaml /opt/anaconda3/envs/sky/bin/sky jobs launch /absolute/path/to/job.yaml -y --async > "/absolute/path/to/logs/$(date +\%d\%m\%y_\%H\%M\%S).log" 2>&1
```

## Run multiple commands
To run multiple commands at once, define the commands as a file:

`cron.sh`
```bash
SKYPILOT_GLOBAL_CONFIG=/absolute/path/to/config/file.yaml
/opt/anaconda3/envs/sky/bin/sky status
/opt/anaconda3/envs/sky/bin/sky jobs launch /absolute/path/to/job1.yaml -y --async
/opt/anaconda3/envs/sky/bin/sky jobs launch /absolute/path/to/job2.yaml -y --async
```
> Using `--async` flag can help launch multiple jobs at once  without waiting for each job to complete before starting the next job.

Then reference the script file in crontab:
```console
*/5 * * * * /absolute/path/to/cron.sh > "/absolute/path/to/logs/$(date +\%d\%m\%y_\%H\%M\%S).log" 2>&1
```

## Run a cronjob against remote API server
Simply specify the following snippet in the config file passed into the cronjob.
```console
api_server:
  endpoint: <API server endpoint>
```
