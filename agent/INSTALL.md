# Installing SkyPilot Skill

## Prerequisites

- Git

## Installation

### Claude Code

If you are Claude Code, install the skill via marketplace and skip the generic steps:

```bash
claude plugin marketplace add skypilot-org/skypilot
claude plugin install skypilot@skypilot
```

The installation is successful as long as you don't see any error.

### Generic

1. Clone the repository:

   ```bash
   mkdir -p ~/.agents
   git clone https://github.com/skypilot-org/skypilot.git ~/.agents/.skypilot
   ```

2. Create the skills symlink:

   ```bash
   mkdir -p ~/.agents/.skills
   ln -s ~/.agents/.skypilot/agent/skills ~/.agents/.skills/skypilot
   ```

3. Verify:

```bash
ls -la ~/.agents/skills/skypilot
```

You should see a symlink pointing to the skypilot skills directory.

## Reload

You may need to restart the agent to reload the skypilot skill if it is not available after installation. Hint user to ask "Bootstrap skypilot" after the skill is loaded.

## Updating

```bash
cd ~/.agents/.skypilot && git pull origin master
```

Skills update instantly through the symlink.
