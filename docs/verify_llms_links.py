"""Check llms.txt links are valid."""

from pathlib import Path
import re
import sys

import requests


def check_url(url, base_url):
    if url.startswith(('http://', 'https://')):
        if 'github.com' in url or 'slack.skypilot.co' in url:
            return True, "external"
        try:
            resp = requests.head(url, timeout=5, allow_redirects=True)
            return resp.status_code < 400, f"HTTP {resp.status_code}"
        except Exception as e:
            return False, str(e)

    if not url.startswith(base_url):
        return False, f"bad format"


def main():
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    script_dir = Path(__file__).parent
    filepath = script_dir / "source" / "llms.txt"
    content = filepath.read_text()

    links = re.findall(r'\[([^\]]+)\]\(([^\)]+)\)', content)
    print(f"Checking {len(links)} links in {filepath.name}")

    if base_url.startswith("http://127.0.0.1"):
        try:
            requests.get(base_url, timeout=1)
        except:
            print(f"WARNING: No server at {base_url}")

    failures = []
    for text, url in links:
        ok, msg = check_url(url, base_url)
        if ok:
            print(f"✓ {text}")
        else:
            print(f"✗ {text}: {msg}")
            failures.append(url)

    print(f"\nResult: {len(links)-len(failures)} OK, {len(failures)} failed")

    if failures:
        print("\nFailed URLs:")
        for url in failures:
            print(f"  {url}")

    return len(failures) > 0


if __name__ == "__main__":
    sys.exit(main())
