#!/usr/bin/env python3
"""Test whether the SkyPilot skill triggers correctly for a set of queries.

Uses --plugin-dir to load the skill as a proper plugin (matching interactive
Claude Code behavior), then checks if Claude invokes the Skill tool.

Note: `claude -p` does NOT load installed plugins (like using-superpowers) that
aggressively encourage skill invocation. This means trigger rates will be lower
than in interactive Claude Code sessions. Running multiple times per query
(--runs) helps account for the stochastic nature of LLM behavior.

Usage:
    python3 skills/test/test_trigger.py
    python3 skills/test/test_trigger.py --timeout 60 --workers 5 --runs 3
"""

import argparse
from collections import defaultdict
from concurrent.futures import as_completed
from concurrent.futures import ProcessPoolExecutor
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
SKILL_PLUGIN_DIR = REPO_ROOT / "skills" / "skypilot"
EVAL_SET_PATH = SCRIPT_DIR / "eval_set.json"


def run_single_query(
    query: str,
    plugin_dir: str,
    project_root: str,
    timeout: int,
    model: str | None = None,
) -> dict:
    """Run a single query and detect if the SkyPilot skill is triggered.

    Returns dict with 'triggered' bool and 'first_tool' name.
    """
    cmd = [
        "claude",
        "-p",
        query,
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--plugin-dir",
        plugin_dir,
    ]
    if model:
        cmd.extend(["--model", model])

    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=project_root,
        env=env,
    )

    triggered = False
    first_tool = None
    current_tool = None  # Track tool being streamed in current content block
    start_time = time.time()
    buffer = ""

    try:
        while time.time() - start_time < timeout:
            if process.poll() is not None:
                remaining = process.stdout.read()
                if remaining:
                    buffer += remaining.decode("utf-8", errors="replace")
                break

            ready, _, _ = select.select([process.stdout], [], [], 1.0)
            if not ready:
                continue

            chunk = os.read(process.stdout.fileno(), 8192)
            if not chunk:
                break
            buffer += chunk.decode("utf-8", errors="replace")

            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue

                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                etype = event.get("type", "")

                if etype == "stream_event":
                    se = event.get("event", {})
                    se_type = se.get("type", "")

                    if se_type == "content_block_start":
                        cb = se.get("content_block", {})
                        if cb.get("type") == "tool_use":
                            current_tool = cb.get("name", "")
                            if first_tool is None:
                                first_tool = current_tool
                            if current_tool == "Skill":
                                triggered = True
                                return {
                                    "triggered": True,
                                    "first_tool": first_tool,
                                    "elapsed": time.time() - start_time,
                                }
                        else:
                            current_tool = None

                    elif se_type == "content_block_delta":
                        delta = se.get("delta", {})
                        if (delta.get("type") == "input_json_delta" and
                                current_tool == "Skill"):
                            pj = delta.get("partial_json", "")
                            if "skypilot" in pj.lower():
                                triggered = True
                                return {
                                    "triggered": True,
                                    "first_tool": first_tool,
                                    "elapsed": time.time() - start_time,
                                }

                    elif se_type == "message_stop":
                        # Don't return here — there may be more turns
                        # after tool execution. Only return on "result".
                        pass

                elif etype == "assistant":
                    msg = event.get("message", {})
                    for ci in msg.get("content", []):
                        if ci.get("type") == "tool_use":
                            tool_name = ci.get("name", "")
                            if first_tool is None:
                                first_tool = tool_name
                            tool_input = ci.get("input", {})
                            if tool_name == "Skill" and "skypilot" in json.dumps(
                                    tool_input).lower():
                                triggered = True
                                return {
                                    "triggered": True,
                                    "first_tool": first_tool,
                                    "elapsed": time.time() - start_time,
                                }
                    # Don't return — more turns may follow after
                    # tool execution.

                elif etype == "result":
                    return {
                        "triggered": triggered,
                        "first_tool": first_tool,
                        "elapsed": time.time() - start_time,
                    }

    finally:
        if process.poll() is None:
            process.kill()
            process.wait()

    return {
        "triggered": triggered,
        "first_tool": first_tool,
        "elapsed": time.time() - start_time,
        "timeout": True,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Test SkyPilot skill triggering")
    parser.add_argument("--timeout",
                        type=int,
                        default=45,
                        help="Timeout per query (seconds)")
    parser.add_argument("--workers",
                        type=int,
                        default=5,
                        help="Parallel workers")
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Runs per query (more runs = more reliable, 3 recommended)")
    parser.add_argument("--threshold",
                        type=float,
                        default=0.5,
                        help="Trigger rate threshold to count as 'triggered'")
    parser.add_argument("--model", default=None, help="Model to use")
    parser.add_argument("--eval-set",
                        default=str(EVAL_SET_PATH),
                        help="Path to eval set JSON")
    parser.add_argument("--plugin-dir",
                        default=str(SKILL_PLUGIN_DIR),
                        help="Path to skill plugin dir")
    args = parser.parse_args()

    eval_set = json.loads(Path(args.eval_set).read_text())
    print(f"Plugin dir: {args.plugin_dir}")
    print(f"Eval set:   {args.eval_set} ({len(eval_set)} queries)")
    print(f"Timeout:    {args.timeout}s per query")
    print(f"Workers:    {args.workers}")
    print(f"Runs/query: {args.runs} (threshold: {args.threshold:.0%})")
    print()

    # Submit all runs (queries * runs_per_query)
    query_results: dict[str, list[dict]] = defaultdict(list)
    query_items: dict[str, dict] = {}

    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        future_to_info = {}
        for item in eval_set:
            query_items[item["query"]] = item
            for run_idx in range(args.runs):
                future = executor.submit(
                    run_single_query,
                    item["query"],
                    args.plugin_dir,
                    str(REPO_ROOT),
                    args.timeout,
                    args.model,
                )
                future_to_info[future] = (item["query"], run_idx)

        for future in as_completed(future_to_info):
            query, run_idx = future_to_info[future]
            try:
                result = future.result()
            except Exception as e:
                result = {
                    "triggered": False,
                    "first_tool": None,
                    "error": str(e)
                }
            query_results[query].append(result)

    # Aggregate results per query
    results = []
    for query, item in query_items.items():
        runs = query_results[query]
        trigger_count = sum(1 for r in runs if r["triggered"])
        total_runs = len(runs)
        trigger_rate = trigger_count / total_runs if total_runs > 0 else 0

        should_trigger = item["should_trigger"]
        if should_trigger:
            passed = trigger_rate >= args.threshold
        else:
            passed = trigger_rate < args.threshold

        results.append({
            "query": query[:70],
            "should_trigger": should_trigger,
            "trigger_rate": trigger_rate,
            "triggers": trigger_count,
            "runs": total_runs,
            "pass": passed,
        })

        status = "PASS" if passed else "FAIL"
        rate_str = f"{trigger_count}/{total_runs}"
        print(
            f"  [{status}] rate={rate_str} expected={should_trigger}: {query[:65]}"
        )

    # Summary
    passed = sum(1 for r in results if r["pass"])
    total = len(results)

    should_trigger_results = [r for r in results if r["should_trigger"]]
    should_not_results = [r for r in results if not r["should_trigger"]]

    # Weighted trigger rates
    pos_triggers = sum(r["triggers"] for r in should_trigger_results)
    pos_runs = sum(r["runs"] for r in should_trigger_results)
    neg_triggers = sum(r["triggers"] for r in should_not_results)
    neg_runs = sum(r["runs"] for r in should_not_results)

    tp = sum(1 for r in results if r["should_trigger"] and r["pass"])
    fn = sum(1 for r in results if r["should_trigger"] and not r["pass"])
    fp = sum(1 for r in results if not r["should_trigger"] and not r["pass"])
    tn = sum(1 for r in results if not r["should_trigger"] and r["pass"])

    print(f"\n{'='*50}")
    print(f"Results: {passed}/{total} queries passed")
    print(f"  True Positive:  {tp} (should trigger, did)")
    print(f"  True Negative:  {tn} (should not, did not)")
    print(f"  False Negative: {fn} (should trigger, did NOT)")
    print(f"  False Positive: {fp} (should not, DID)")
    print()
    if pos_runs > 0:
        print(
            f"  Should-trigger rate:     {pos_triggers}/{pos_runs} = {pos_triggers/pos_runs:.0%}"
        )
    if neg_runs > 0:
        print(
            f"  Should-NOT-trigger rate: {neg_triggers}/{neg_runs} = {neg_triggers/neg_runs:.0%}"
        )
    print()
    if tp + fp > 0:
        print(f"  Precision: {tp/(tp+fp):.0%}")
    if tp + fn > 0:
        print(f"  Recall:    {tp/(tp+fn):.0%}")
    print(f"  Accuracy:  {(tp+tn)/total:.0%}")


if __name__ == "__main__":
    main()
