# OpenClaw on SkyPilot

Deploy [OpenClaw](https://github.com/openclaw/openclaw) - a personal AI assistant for WhatsApp, Telegram, Slack, Discord, and more - on a cloud VM via SkyPilot.

![Local vs Cloud](https://raw.githubusercontent.com/skypilot-org/skypilot/master/examples/openclaw/local_vs_cloud.png)

This gives you an isolated sandbox environment separate from your local machine, with the built-in WebChat interface accessible via SSH tunnel.

## Prerequisites

- [SkyPilot](https://docs.skypilot.co/) installed and cloud credentials configured (`sky check`)
- An [Anthropic API key](https://console.anthropic.com/)

## Quickstart

1. Launch the gateway:

```bash
sky launch -c openclaw openclaw.yaml --secret ANTHROPIC_API_KEY
```

2. Open an SSH tunnel and access WebChat:

```bash
ssh -L 18789:localhost:18789 openclaw
```

The WebChat port is bound to `localhost` on the VM and is not exposed to the public internet - it is only reachable through the SSH tunnel.

Open http://localhost:18789 in your browser. On first launch, the gateway auth token is printed in the setup logs - enter it in the WebChat Settings panel to connect. On subsequent launches, the token is reused from the persisted config; retrieve it with `grep token ~/.openclaw/openclaw.json`.

<p align="center">
  <img src="https://i.imgur.com/gdF6JDX.png" alt="OpenClaw Gateway Dashboard" width="600">
</p>

<p align="center">
  <img src="https://i.imgur.com/YHCnb7C.png" alt="OpenClaw WebChat interface" width="600">
</p>

## Next steps

Once the gateway is running and you can access WebChat, here are the key ways to make your deployment more useful.

### Connect messaging channels

SSH into the cluster and run the onboarding wizard, or configure channels individually:

```bash
ssh openclaw

# Interactive wizard - walks through all available channels
openclaw onboard

# Or connect channels directly:
openclaw channels login --channel whatsapp   # scan QR code to link
openclaw channels login --channel telegram   # requires a BotFather token
openclaw channels login --channel discord
```

### Lock down access

The default configuration uses `pairing` mode - unknown senders receive a code and are ignored until you approve them. Review and approve pending requests:

```bash
openclaw pairing list whatsapp
openclaw pairing approve whatsapp <CODE>
```

### Customize the agent persona

OpenClaw reads context files from the workspace directory on the first turn of each session. SSH in and edit these to shape behavior:

```bash
ssh openclaw
cd ~/.openclaw/workspace  # or wherever agents.defaults.workspace points

# Key files:
#   AGENTS.md   - operating instructions and memory
#   SOUL.md     - persona, tone, and boundaries
#   TOOLS.md    - tool usage notes
#   IDENTITY.md - agent name and emoji
#   USER.md     - user profile and preferred form of address
```

### Set up cron jobs

Schedule recurring tasks that run inside the gateway:

```bash
ssh openclaw
openclaw cron add --name "Morning brief" \
  --cron "0 7 * * *" \
  --tz "America/Los_Angeles" \
  --session isolated \
  --message "Summarize my calendar and unread messages"
```

For full configuration reference, see [docs.openclaw.ai](https://docs.openclaw.ai/).

## Managing the cluster

Use `sky stop` to pause the cluster while preserving all state on disk. When you're ready to resume, `sky start` brings it back with everything intact:

```bash
# Pause the cluster (state preserved on disk)
sky stop openclaw

# Resume later
sky start openclaw
```

To permanently tear down the cluster and its disk:

```bash
sky down openclaw
```

> **Note:** `sky down` deletes the VM and its disk, so any state stored locally on the VM will be lost. If you want to preserve state across `sky down`, see [Appendix: Persistent storage with S3](#appendix-persistent-storage-with-s3).

## Appendix: Persistent storage with S3

If you prefer using `sky down` (which fully deletes the VM) instead of `sky stop`, you can persist OpenClaw state to an S3 bucket so that nothing is lost between teardowns.

1. Create an S3 bucket (one-time):

```bash
aws s3 mb s3://openclaw-state
```

2. Add the following to `openclaw.yaml`:

```yaml
envs:
  OPENCLAW_BUCKET: openclaw-state

file_mounts:
  ~/.openclaw:
    source: s3://${OPENCLAW_BUCKET}
    mode: MOUNT_CACHED
```

`MOUNT_CACHED` uses rclone with local disk caching, which supports full POSIX write operations (including the append writes OpenClaw uses for session transcripts) and asynchronously syncs changes back to S3.

With this setup, you can `sky down openclaw` and later `sky launch` again - the gateway picks up right where it left off with all channels connected and history intact.

To use a custom bucket name:

```bash
sky launch -c openclaw openclaw.yaml \
  --env ANTHROPIC_API_KEY \
  --env OPENCLAW_BUCKET=my-openclaw-bucket
```

To permanently delete all persisted data:

```bash
aws s3 rb s3://openclaw-state --force
```
