#!/usr/bin/env python3
"""Verify that --plugin-dir fixes skill loading in claude -p."""

import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time


def main():
    project_root = Path(__file__).resolve().parent.parent.parent
    skill_plugin_dir = project_root / "skills" / "skypilot"

    print(f"Project root: {project_root}")
    print(f"Plugin dir:   {skill_plugin_dir}")

    query = (
        "I need to fine-tune Llama 3.1 70B on my custom dataset. "
        "Can you write me a task YAML that uses 8xH100s with spot instances?")

    cmd = [
        "claude",
        "-p",
        query,
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--plugin-dir",
        str(skill_plugin_dir),
        "--debug-file",
        "/tmp/skill-debug.log",
    ]

    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    print(f"\nRunning claude -p with --plugin-dir...")
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=str(project_root),
        env=env,
    )

    start = time.time()
    buffer = ""
    triggered = False

    try:
        while time.time() - start < 30:
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

                etype = event.get("type", "?")
                if etype == "stream_event":
                    se = event.get("event", {})
                    se_type = se.get("type", "")
                    if se_type == "content_block_start":
                        cb = se.get("content_block", {})
                        tool = cb.get("name", "")
                        print(
                            f"  [{time.time()-start:.1f}s] BLOCK_START: type={cb.get('type')}, name={tool}"
                        )
                        if tool == "Skill":
                            triggered = True
                    elif se_type == "content_block_delta":
                        delta = se.get("delta", {})
                        if delta.get("type") == "input_json_delta":
                            pj = delta.get("partial_json", "")
                            if "skypilot" in pj.lower():
                                print(
                                    f"  [{time.time()-start:.1f}s] JSON_DELTA: {pj[:200]}"
                                )
                                triggered = True
                    elif se_type == "message_stop":
                        print(f"  [{time.time()-start:.1f}s] message_stop")
                        break
                elif etype == "result":
                    cost = event.get("cost_usd", "?")
                    print(f"  [{time.time()-start:.1f}s] RESULT: cost=${cost}")
                    break
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()

    print(f"\nTriggered: {triggered}")
    print(f"Elapsed: {time.time()-start:.1f}s")

    # Check debug log for plugin loading
    debug_log = Path("/tmp/skill-debug.log")
    if debug_log.exists():
        content = debug_log.read_text()
        print(f"\n=== Plugin-related debug lines ===")
        for line in content.split("\n"):
            lower = line.lower()
            if any(kw in lower for kw in ["plugin", "skill", "skypilot"]):
                print(f"  {line.strip()}")
        debug_log.unlink()


if __name__ == "__main__":
    main()
