# NemoClaw on SkyPilot

Deploy [NemoClaw](https://github.com/NVIDIA/NemoClaw) — NVIDIA's sandboxed OpenClaw runtime — on a cloud VM via SkyPilot.

NemoClaw wraps OpenClaw in an isolated [OpenShell](https://github.com/NVIDIA/OpenShell) sandbox with filesystem isolation, network egress control, and inference routing. Running it on a dedicated cloud VM adds a fourth layer: the machine itself contains none of your personal data or credentials.

Read the full blog post: [Make NemoClaw Bulletproof]()

![NemoClaw Local vs Cloud](https://raw.githubusercontent.com/skypilot-org/skypilot/master/examples/nemoclaw/nemo-local-cloud.png)

## Prerequisites

- [SkyPilot](https://docs.skypilot.co/) installed and cloud credentials configured (`sky check`)
- An [NVIDIA API key](https://build.nvidia.com/settings/api-keys) (free)

## Quickstart

1. Launch the VM:

```bash
sky launch -c nemoclaw nemoclaw.yaml
```

2. SSH in and run the interactive installer:

```bash
ssh nemoclaw
cd ~/NemoClaw &&  ./install.sh
```

When prompted:

- **Sandbox name** — hit Enter to accept `my-assistant`
- **NVIDIA API key** — paste your key from [build.nvidia.com/settings/api-keys](https://build.nvidia.com/settings/api-keys)
- **Policy presets** — select desired presets

3. Connect to your sandbox and open the TUI:

```bash
source ~/.bashrc
nemoclaw my-assistant connect
openclaw tui
```

The first response after a fresh install can take a minute or two while the inference route initializes cold.

## Connect messaging channels

To connect Telegram, create a bot with [@BotFather](https://t.me/BotFather) and copy the token. Then exit the sandbox and start the bridge:

```bash
exit
TELEGRAM_BOT_TOKEN=<your-token> nemoclaw start telegram
```

## Manage the cluster

```bash
# Pause the VM when you're not using it (state preserved on disk)
sky stop nemoclaw

# Resume later — NemoClaw state and config intact
sky start nemoclaw

# Tear down entirely
sky down nemoclaw
```

The autostop timer activates only after you exit the SSH session. While you're connected, the cluster stays up.

`sky stop` is the right default for personal use — you're not paying for compute while idle, and everything picks up where you left off when you resume.
