# Slack Notifications for SkyPilot Managed Jobs

This example shows how to add Slack notifications to your SkyPilot managed jobs using built-in notification handlers or custom bash scripts.

## Setup

1. **Create a Slack Webhook URL:**
   - Go to https://api.slack.com/messaging/webhooks
   - Create a new webhook for your Slack workspace
   - Copy the webhook URL

2. **Set the environment variable:**
   ```bash
   export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/YOUR/WEBHOOK/URL"
   ```

## Examples

### Built-in Notifications (Recommended)

**Simple Slack notifications:**
```bash
sky jobs launch builtin_slack_notification.yaml
```

**Multiple notification services:**
```bash
sky jobs launch multiple_notifications.yaml
```

**Custom message templates:**
```bash
sky jobs launch custom_messages.yaml
sky jobs launch simple_custom_message_example.yaml
```

### Custom Bash Scripts

**Advanced customization:**
```bash
sky jobs launch basic_slack_notification.yaml
```

## Built-in vs Custom

| Feature | Built-in Notifications | Custom Bash Scripts |
|---------|----------------------|-------------------|
| **Ease of use** | ✅ Simple YAML config | ❌ Write bash/curl |
| **Error handling** | ✅ Built-in retry logic | ❌ Manual implementation |
| **Multiple services** | ✅ Easy array syntax | ❌ Complex scripting |
| **Customization** | ⚠️ Predefined templates | ✅ Full control |

## Built-in Notification Options

### Slack
```yaml
event_callback:
  - slack:
      webhook_url: ${SLACK_WEBHOOK_URL}    # Required
      username: "SkyPilot Bot"             # Optional
      channel: "#ml-jobs"                  # Optional
      notify_on: ["FAILED", "SUCCEEDED"]   # Optional: filter statuses
      message: "{EMOJI} Job {TASK_NAME} is {JOB_STATUS}"  # Optional: custom message
```

### Discord
```yaml
event_callback:
  - discord:
      webhook_url: ${DISCORD_WEBHOOK_URL}  # Required
      username: "SkyPilot"                 # Optional
      notify_on: ["RUNNING", "FAILED"]     # Optional: filter statuses
      message: "{EMOJI} {TASK_NAME} → {JOB_STATUS}"  # Optional: custom message
```

### Multiple Services
```yaml
event_callback:
  - slack:
      webhook_url: ${SLACK_WEBHOOK_URL}
  - discord:
      webhook_url: ${DISCORD_WEBHOOK_URL}
```

## Custom Message Templates

You can customize notification messages using template variables. Use either `{VARIABLE}` or `$VARIABLE` syntax:

### Available Variables

- `{JOB_STATUS}` - Current job status (STARTING, RUNNING, SUCCEEDED, etc.)
- `{JOB_ID}` - Unique job identifier
- `{TASK_ID}` - Task ID within the job
- `{TASK_NAME}` - Name of the task
- `{CLUSTER_NAME}` - Name of the cluster running the job
- `{EMOJI}` - Status-appropriate emoji (🚀, ⚡, ✅, ❌, etc.)

### Template Examples

```yaml
# Simple text message
message: "{EMOJI} {TASK_NAME} is now {JOB_STATUS} on {CLUSTER_NAME}"

# Detailed status message
message: "Job #{JOB_ID}: {TASK_NAME} → {JOB_STATUS} {EMOJI} (Cluster: {CLUSTER_NAME})"
```

The `message` field replaces the entire notification with simple text. Without it, you get rich formatting with job details.

## Available Status Values

Notifications can be triggered on these job statuses:
- `STARTING` - Job is starting up
- `RUNNING` - Job is actively running
- `SUCCEEDED` - Job completed successfully
- `FAILED` - Job failed
- `RECOVERING` - Job is recovering from interruption
- `CANCELLED` - Job was cancelled by user

Use the `notify_on` field to filter which statuses trigger notifications.