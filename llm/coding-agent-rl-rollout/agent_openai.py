"""In-sandbox coding agent that drives an OpenAI-compatible model endpoint.

This runs *inside* a rollout sandbox and dials the model server over HTTP (the
``--base-url`` points at the server's ``/v1`` endpoint). It reads the bug,
asks the model for the corrected file, and writes it back. Kept dependency-light
(``openai`` + stdlib) so it installs quickly in a fresh sandbox.

Usage (inside the sandbox):
    python agent_openai.py --base-url http://<model-server-ip>:8000/v1 \\
        --model model --workdir /home/agent/repo
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time

PROMPT_TEMPLATE = """You are fixing a bug in a small Python project.

PROBLEM:
{problem}

Current contents of `calc.py`:
```python
{calc}
```

Return the COMPLETE corrected contents of `calc.py` and nothing else, in a
single ```python code block. Only fix the bug described; do not change the
public API or edit the tests.
"""

_CODE_BLOCK = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)


def _extract_code(text: str) -> str | None:
    blocks = _CODE_BLOCK.findall(text or "")
    if not blocks:
        return None
    # The corrected file is the longest python block (avoids stray snippets).
    return max(blocks, key=len).strip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url",
                    required=True,
                    help="OpenAI-compatible /v1 base URL")
    ap.add_argument("--model", default="model")
    ap.add_argument("--workdir", default="/home/agent/repo")
    ap.add_argument("--api-key",
                    default=os.environ.get("OPENAI_API_KEY", "vime"))
    ap.add_argument("--timeout", type=float, default=120.0)
    args = ap.parse_args()

    # Imported here so a missing dep gives a clear message in the sandbox.
    from openai import OpenAI

    calc_path = os.path.join(args.workdir, "calc.py")
    problem_path = os.path.join(args.workdir, "PROBLEM_STATEMENT.md")
    with open(calc_path) as f:
        calc = f.read()
    problem = ""
    if os.path.exists(problem_path):
        with open(problem_path) as f:
            problem = f.read()

    client = OpenAI(base_url=args.base_url,
                    api_key=args.api_key,
                    timeout=args.timeout)
    t0 = time.time()
    print(f"[agent] calling {args.base_url} (model={args.model})...",
          flush=True)
    resp = client.chat.completions.create(
        model=args.model,
        messages=[
            {
                "role": "system",
                "content": "You are a precise senior Python engineer."
            },
            {
                "role": "user",
                "content": PROMPT_TEMPLATE.format(problem=problem, calc=calc)
            },
        ],
        temperature=0.0,
        max_tokens=2048,
    )
    text = resp.choices[0].message.content or ""
    print(f"[agent] model responded in {time.time() - t0:.1f}s", flush=True)

    fixed = _extract_code(text)
    if not fixed:
        print("[agent] ERROR: no code block in model response:\n" + text[:500],
              file=sys.stderr)
        return 2
    with open(calc_path, "w") as f:
        f.write(fixed)
    print(f"[agent] wrote {len(fixed)} bytes to calc.py", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
