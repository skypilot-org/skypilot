# Installing SkyPilot Skill

## Prerequisites

- Git

## Installation

### Claude Code

If you are Claude Code, you can install the skill via marketplace and skip the generic steps:

```bash
claude plugin marketplace add skypilot-org/skypilot#skill-doc
claude plugin install skypilot@skypilot
```

### Generic

1. Clone the repository:

   ```bash
   mkdir -p ~/.agents
   git clone https://github.com/skypilot-org/skypilot.git --branch skill-doc ~/.agents/.skypilot
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

If `skypilot` skill is not available after successful installation, you need to (or ask user to) restart the agent to reload the skills. Hint user to ask "Bootstrap skypilot" after they are back.

## Updating

```bash
cd ~/.agents/.skypilot && git pull
```

Skills update instantly through the symlink.
