# Installing SkyPilot Skill

## Prerequisites

- Git

## Installation

### Claude Code

If you are Claude Code, you can install the skill via marketplace:

```
/plugin marketplace add skypilot-org/skypilot#skill-doc
/plugin install skypilot@skypilot
```

### Generic

1. **Clone the repository:**

   ```bash
   mkdir -p ~/.agents
   git clone https://github.com/skypilot-org/skypilot.git --branch skill-doc ~/.agent/.skypilot
   ```

2. **Create the skills symlink:**

   ```bash
   mkdir -p ~/.agents/.skills
   ln -s ~/.agents/.skypilot/agent/skills ~/.agents/.skills/skypilot
   ```

**Restart the agent** to load the skill.

## Verify

```bash
ls -la ~/.agents/skills/skypilot
```

You should see a symlink pointing to the skypilot skills directory.

## Updating

```bash
cd ~/.agents/.skypilot && git pull
```

Skills update instantly through the symlink.
