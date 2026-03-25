# Autonomous Code Optimization with SkyPilot

Run [autoresearch](https://github.com/karpathy/autoresearch) / [pi-autoresearch](https://github.com/davebcn87/pi-autoresearch)-style optimization loops against **any open-source project**, enhanced with academic literature search and parallelized across cloud VMs via SkyPilot. A local coding agent uses the [SkyPilot skill](https://docs.skypilot.co/en/latest/getting-started/skill.html) to spin up VMs, submit experiments, and parallelize work across multiple VMs.

For a deep dive into methodology and results (llama.cpp CPU inference: +49% text generation throughput), check out the **[blog post](https://blog.skypilot.co/autonomous-code-optimization/)**.

## How It Works

```
Local Agent (Claude Code, Codex, etc.)
  |
  |-- uses the SkyPilot skill to:
  |     - launch cloud VMs (sky launch)
  |     - submit experiments (sky exec + experiment.yaml)
  |     - check results (sky logs / ssh)
  |
  |-- searches literature (arxiv, forks, PRs) for optimization ideas
  |-- profiles the codebase to identify bottlenecks
  |-- edits source code, fans out experiments in parallel
  |-- keeps winners, discards losers, re-profiles, repeats
```

The agent writes its own benchmark script (`autoresearch.sh`) and correctness checks (`autoresearch.checks.sh`), then runs the loop: profile -> search literature -> experiment -> commit winners -> repeat.

## Files

| File | Purpose |
|------|---------|
| `experiment.yaml` | SkyPilot task template: builds, benchmarks, and checks one experiment on a cloud VM |
| `instructions.md` | Full agent instructions (give this to your coding agent) |
| `setup.sh` | One-command setup: installs SkyPilot, clones target repo, downloads files |

## Quick Start

```bash
# 1. Clone your target project
git clone https://github.com/<org>/<project>.git
cd <project>

# 2. Download the experiment template and agent instructions
curl -fsSL https://raw.githubusercontent.com/skypilot-org/skypilot/master/examples/autonomous-code-optimization/experiment.yaml -o experiment.yaml
curl -fsSL https://raw.githubusercontent.com/skypilot-org/skypilot/master/examples/autonomous-code-optimization/instructions.md -o instructions.md

# 3. Tell your agent to start
#    "Read instructions.md and optimize <project> for <metric>.
#     Use 4 SkyPilot VMs. Use AWS infra."
```

Or use the one-line setup:

```bash
export TARGET_REPO="https://github.com/<org>/<project>.git"
curl -fsSL https://raw.githubusercontent.com/skypilot-org/skypilot/master/examples/autonomous-code-optimization/setup.sh | bash
```

The [SkyPilot skill](https://docs.skypilot.co/en/latest/getting-started/skill.html) handles installation, credential setup, and all VM operations.

## How It Differs from the GPU Autoresearch Example

| | [GPU Autoresearch](https://github.com/skypilot-org/skypilot/tree/master/examples/autoresearch) | This Example |
|---|---|---|
| Target | ML training (karpathy/autoresearch) | Any OSS project |
| Compute | GPU clusters (H100/H200) | CPU VMs (cheap) |
| Search strategy | Agent brainstorms from code context | Agent reads papers + profiles bottlenecks |
| Cost | ~$300/8hr (GPU) | ~$20 (CPU VMs) + ~$9 (API) |
