# Flying Roadster: RL post-training an LLM to pilot a car in real time

An RL environment for two flying-car stunts, built on a small physics simulator, where an LLM
is the pilot: every 0.2 s of simulated time it reads one line of telemetry and replies with one
JSON command, and the car keeps moving while it thinks. We use it to compare the latest LLMs
against an open 4B model, and to RL post-train that 4B (SkyRL, GRPO + LoRA) with SkyPilot.

Everything here is simulation. It is not affiliated with Tesla and is not based on any
real vehicle's software or data; "roadster" just means a generic two-seat car.

| Stunt | What the pilot has to do |
|---|---|
| Half-loop launch flip | Leave a curled ramp upside down at 10 to 14 m, roll back upright, land on the road |
| Cybertruck kickflip | From 50 to 95 km/h, clear a parked Cybertruck, do a full 360° roll, land on the road |

Wind presets: light wind, gale and storm (mean wind with a ground boundary layer plus gusts
the pilot can't see in advance). A landing only counts with all four wheels on the 14 m road.

## Results (test seeds 9201 / 9202 / 9203, temperature 0, % of flights that land)

192 flights per stunt in light wind and in a gale, 384 in a storm; every row flies the same tasks.

| Pilot | Cybertruck kickflip: light / gale / storm | Ramp flip: light / gale / storm |
|---|---|---|
| DeepSeek V4.1 Flash, real time | 0 / 0 / 0 | 1 / 2 / 3 |
| DeepSeek V4.1 Flash, unlimited time to think | 98 / 90 / 87 | 82 / 78 / 79 |
| Scripted pilot (decides instantly) | 98 / 83 / 61 | 100 / 100 / 96 |
| Qwen3-4B-Instruct, untrained (real time) | 0 / 0 / 0 | 100 / 99 / 85 |
| Qwen3-4B, warm start only | 97 / 93 / 84 | 100 / 100 / 96 |
| **Qwen3-4B, warm start + RL (real time)** | **96 / 96 / 90** | **100 / 100 / 98** |

The trained pilot decides in ~0.09 s with one car per H100 (LoRA merged into the base weights),
inside the 0.2 s interval, so it scores the same in real time and with the environment waiting.
"Unlimited time" means the environment waits for every reply. The checkpoint was picked on a
separate validation split (seeds 2001-2003) and the test seeds were flown once.

The recipe (the `train.yaml` defaults): a warm start on 64 successful scripted-pilot flights per
stunt in light wind plus 350 gale and storm Cybertruck flights that DeepSeek V4.1 Flash landed on
training seeds with the environment waiting (`distill_data/`), one epoch (~2 h 20 min on one H100,
mostly optimizer offload); then GRPO with a rank-64 LoRA, 16 tasks x 8 flights per step for 60
steps across all three weathers, storm counted twice and half the Cybertruck flights slow
approaches (~1 h 35 min).

## Run it

Train (warm start + RL) on one H100; tries your Kubernetes cluster first, then AWS spot:

```bash
export WANDB_API_KEY=...
sky jobs launch train.yaml --secret WANDB_API_KEY --env OUTPUT_BUCKET=my-roadster-bucket \
  --env RUN=pilot-run1
```

Or run training, rollout inference and evaluation as one SkyPilot Job Group: the trainer and a
vLLM server generating the rollouts each get an H100 and share a volume for the per-step LoRA
weights, and an eval watcher launches an eval of every checkpoint (on its own GPU, in parallel)
from inside the group, then flies the held-out seeds once with the best one. Needs a Kubernetes
cluster with a ReadWriteMany storage class (set it in `policy_volume.yaml`):

```bash
sky volumes apply policy_volume.yaml
sky jobs launch train_eval.yaml --secret WANDB_API_KEY --env OUTPUT_BUCKET=my-roadster-bucket \
  --env RUN=pilot-run1
```

Evaluate a checkpoint yourself (vLLM on one H100, LoRA merged into the warm-started base; same
harness as for API models). Pick it on the validation seeds, then fly the test seeds once; the
table above used `EVAL_SEEDS=9201,9202,9203`, `PER_KIND=192` for light wind and gale and 384 for
the storm, `MODES=realtime`. `--env MODEL=Qwen/Qwen3-4B-Instruct-2507 --env STEPS="" --env
SKIP_BASE=0` evaluates the untrained model instead:

```bash
sky jobs launch eval.yaml --secret WANDB_API_KEY --env OUTPUT_BUCKET=my-roadster-bucket \
  --env RUN=pilot-run1 --env MODEL=/outputs/pilot-run1/sft_model --env STEPS="60" --env SKIP_BASE=1 \
  --env EVAL_SEEDS=2001,2002,2003
```

Benchmark any OpenAI-compatible model as the pilot (add `--seeds 9201,9202,9203` to fly the test set from the
table, with `--per-kind 192` for light wind and gale and 384 for `--weather storm`; the default seeds are a
separate held-out set). The default endpoint is SkyPilot Tokens
(https://tokens.skypilot.ai); `--endpoint openrouter` uses `$OPENROUTER_API_KEY` instead, and any
other server takes `--base-url` (plus `--api-key-env` if its key lives in another variable; a local
server needs no key). Drop `--realtime` to give the model unlimited time to think:

```bash
SKYPILOT_TOKENS_API_KEY=... python tools/pilot_bench.py --model deepseek-ai/DeepSeek-V4.1-Flash \
  --realtime --temperature 0 --per-kind 12 --out /tmp/pilot_deepseek
```

Render a flight to video (headless Chromium via Playwright):

```bash
python render_video.py flight.mp4 /tmp/pilot_deepseek/clips/hop_kickflip__12.json --label "DeepSeek · real time"
```

## Layout

| Path | What |
|---|---|
| `roadster_env/` | Physics (500 Hz), stunts, wind, reward; `pilot.py` = the 0.2 s command interface; `pilot_baselines.py` = scripted pilots |
| `prompts/`, `prompting.py` | Pilot briefing and prompts |
| `skyrl_roadster/` | SkyRL environment (`pilot_env.py`), dataset builder, warm-start data builder, install scripts |
| `tools/pilot_bench.py` | Real-time / turn-based benchmark for API models and local vLLM servers; saves renderable clips |
| `tools/merge_lora.py` | Merges a LoRA checkpoint into its base model for serving |
| `train.yaml`, `eval.yaml` | SkyPilot jobs |
| `train_eval.yaml`, `policy_volume.yaml` | The same recipe as one SkyPilot Job Group: trainer, a vLLM inference task generating the rollouts (`skyrl_roadster/serve_and_publish.sh`), and an eval watcher (`tools/eval_watcher.py`) that launches an eval of every checkpoint from inside the group |
| `distill_data/` | The teacher flights for the warm start: DeepSeek V4.1 Flash's landed gale and storm Cybertruck flights (task + commands) |
| `viewer/`, `render_video.py` | Three.js replay viewer and MP4 renderer |
| `tests/` | `python -m pytest tests -q` |

## Knobs worth knowing

Each earlier attempt is one knob away from the defaults:

- `SFT_FLIGHTS=0` trains with RL from the base model. In our runs it never got off the ground
  on the Cybertruck: every sample rolled with its wheels on the road and crashed the same way,
  so GRPO had nothing to compare.
- `HANDOVER=""` turns off the partway starts (reverse curriculum). On their own, without the
  warm start, they did not transfer to flights from t=0 for us.
- `TRAIN_WEATHERS=normal` trains in light wind only, and `HARD_FRAC=0` drops the slow, far-truck
  Cybertruck flights. The blog post explains why the final run uses both.
- `SFT_ROUND=1` rounds the warm-start commands to one decimal. Decisions get faster (the full
  precision default makes the pilot write ~15 decimal places per landing command), but in our
  rerun the real-time ramp got worse.
