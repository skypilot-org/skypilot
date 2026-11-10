"""SWE-smith -> a dataset-agnostic sample schema (the headline workload).

SWE-smith (SWE-bench/SWE-smith on HF, 50k instances / 128 repos) is real-repo
bug-fixing with executable per-repo environments. This loader maps its instances
onto the SAME schema generate.py already consumes for MBPP (see generate.py
_get_metadata), so the rollout fn / adapter / masking are UNCHANGED — only the
dataset, the images, and the grader differ.

Verified instance fields (huggingface.co/datasets/SWE-bench/SWE-smith):
    instance_id, repo, patch (GOLD fix), FAIL_TO_PASS, PASS_TO_PASS,
    base_commit, image_name (per-REPO env image), problem_statement, created_at

Mapping to sample metadata:
    task        <- problem_statement
    image       <- image_name           (per-repo; drives one warm pool per repo)
    pool        <- sanitized repo name   (pool-per-repo)
    workdir     <- /testbed              (swesmith ENV_NAME; SWE-bench lineage)
    setup_cmd   <- establish the BUGGY state in /testbed  (see OPEN QUESTION)
    eval_files  <- __eval__.py grader + spec.json (F2P/P2P/repo)
    eval_cmd    <- python __eval__.py    (exit 0 == resolved)
    gold_patch  <- patch                 (for the golden-patch harness self-check)

Three things differ from MBPP:
  1. PER-REPO POOLS/IMAGES: emit_pools() lists the distinct (pool, image) pairs
     so the launch YAML can create one warm pool per repo image.
  2. BUGGY-STATE SETUP: the per-repo image is shared across many bugs, so the
     specific bug must be applied per rollout BEFORE the agent runs, via the new
     metadata["setup_cmd"] hook generate.py runs post-claim, pre-agent.
     RESOLVED (swesmith/profiles/base.py): the bug is a git BRANCH named exactly
     `instance_id` in the mirror baked into the per-repo image →
     `git -C /testbed checkout -f {instance_id}`.
  3. GRADER via swesmith's OWN registry (don't reinvent per-repo test commands).
     RESOLVED (base.py): registry.get_from_inst(inst) → get_test_cmd(inst) →
     log_parser(output); pass == swebench TestStatus.PASSED. The staged
     __eval__.py uses exactly this (needs swesmith+swebench importable in-sandbox).
"""

from __future__ import annotations

import argparse
import json
import re

WORKDIR = "/testbed"


def _pool_name(repo: str, suffix: str = "") -> str:
    """One pool per repo image. Sanitize 'owner/name' -> a k8s-safe pool id.

    ``suffix`` (a run-unique token, e.g. the Job Group name) makes the pool name
    per-RUN so each run OWNS its own pool — creates it, uses it, deletes it at end
    (self-cleanup, no cross-run sharing/leak). Omit for the shared-by-repo behavior.
    """
    base = "swesmith-" + re.sub(r"[^a-z0-9]+", "-", repo.lower()).strip("-")
    if suffix:
        suf = re.sub(r"[^a-z0-9]+", "-", suffix.lower()).strip("-")
        # Server caps the pool/template name at 58 chars (RFC1123 63 - len("-pool"),
        # see sandbox/core.py) — NOT 63. Reserve room for the suffix so the
        # run-unique part is NEVER truncated (else two runs' pools could collide).
        base = base[:58 - len(suf) - 1].strip("-") + "-" + suf
    return base[:58].strip("-")


# Reward is graded HOST-side in generate._evaluate_swesmith (swesmith/swebench
# are NOT in the env images — proven by the golden-patch smoke). So the dataset
# precomputes the per-repo test command here (get_test_cmd, deterministic per
# instance) and carries the instance fields log_parser needs; generate.py runs
# the cmd in the sandbox and parses in-process. API from swesmith/profiles/base.py:
#   cmd, _ = registry.get_from_inst(inst).get_test_cmd(inst)


def _test_cmd_for(inst: dict) -> str:
    """Precompute the repo test command via swesmith's registry (host-side, at
    dataset-gen — the trainer has swesmith installed). Deterministic per instance."""
    from swesmith.profiles import registry

    cmd, _ = registry.get_from_inst(inst).get_test_cmd(inst)
    return cmd


def row_to_sample(row: dict, pool_suffix: str = "") -> dict:
    repo = row["repo"]
    instance_id = row["instance_id"]
    # The instance fields generate._evaluate_swesmith needs for host-side parsing.
    inst = {
        "instance_id": instance_id,
        "repo": repo,
        "image_name": row.get("image_name", ""),
        "base_commit": row.get("base_commit", ""),
        "FAIL_TO_PASS": list(row.get("FAIL_TO_PASS") or []),
        "PASS_TO_PASS": list(row.get("PASS_TO_PASS") or []),
    }
    return {
        "text": row["problem_statement"],
        "label": instance_id,
        "metadata": {
            "instance_id": instance_id,
            "task": row["problem_statement"],
            "repo": repo,
            "base_commit": row.get("base_commit", ""),
            "image": row["image_name"],
            "pool": _pool_name(repo, pool_suffix),
            "workdir": WORKDIR,
            # O1 RESOLVED (swesmith/profiles/base.py): the bug lives on a git
            # BRANCH named exactly instance_id in the repo mirror baked into the
            # per-repo image. `git checkout <instance_id>` reaches the buggy state
            # (bug present, hidden tests removed — agent works blind to tests).
            "setup_cmd": f"git -C {WORKDIR} checkout -f {instance_id}",
            "files": {
            },  # repo pre-baked in the image; nothing to stage pre-loop
            # Host-parsed grading: generate.py reapplies the
            # agent's fix onto HEAD~1 (tests present), runs test_cmd, parses via
            # swesmith log_parser. No in-sandbox grader.
            "eval_kind": "swesmith",
            "test_cmd": _test_cmd_for(inst),
            "swesmith_inst": inst,
            "gold_patch": row.get(
                "patch", ""),  # golden-patch harness self-check (smoke)
        },
    }


def emit_pools(rows: list[dict]) -> list[dict]:
    """Distinct (pool, image) pairs for warm-pool creation (one per repo image)."""
    seen: dict[str, str] = {}
    for r in rows:
        m = r["metadata"]
        seen.setdefault(m["pool"], m["image"])
    return [{"pool": p, "image": img} for p, img in seen.items()]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, help="output JSONL path")
    ap.add_argument("--pools-out",
                    help="optional: write distinct (pool,image) JSON here")
    ap.add_argument("--dataset", default="SWE-bench/SWE-smith")
    ap.add_argument("--split", default="train")
    ap.add_argument("--repos",
                    nargs="*",
                    default=None,
                    help="subset to these repos (owner/name); default = all")
    ap.add_argument("--per-repo",
                    type=int,
                    default=0,
                    help="cap instances per repo (0 = all)")
    ap.add_argument("--limit", type=int, default=0, help="global cap (0 = all)")
    ap.add_argument(
        "--pool-suffix",
        default="",
        help=
        "run-unique token appended to pool names → each run owns+cleans its own pool"
    )
    args = ap.parse_args()

    from datasets import load_dataset  # deferred: heavy import

    ds = load_dataset(args.dataset, split=args.split)
    # SUBSTRING match (case-insensitive): pass bare tokens ("markupsafe"); the
    # dataset `repo` is a mirror string, not "owner/name".
    tokens = [t.lower() for t in (args.repos or [])]
    per_repo_count: dict[str, int] = {}
    rows: list[dict] = []
    for row in ds:
        row = dict(row)
        if tokens and not any(t in row["repo"].lower() for t in tokens):
            continue
        if args.per_repo:
            c = per_repo_count.get(row["repo"], 0)
            if c >= args.per_repo:
                continue
            per_repo_count[row["repo"]] = c + 1
        rows.append(row_to_sample(row, args.pool_suffix))
        if args.limit and len(rows) >= args.limit:
            break

    with open(args.out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    pools = emit_pools(rows)
    if args.pools_out:
        with open(args.pools_out, "w", encoding="utf-8") as f:
            json.dump(pools, f)
    print(f"wrote {len(rows)} samples to {args.out}; {len(pools)} pools: " +
          ", ".join(p["pool"] for p in pools))


if __name__ == "__main__":
    main()
