#!/usr/bin/env python3
"""Validate llms.txt format."""

import re
import sys
from pathlib import Path


def validate_llms_txt(filepath):
    content = Path(filepath).read_text()
    lines = content.strip().split('\n')
    errors = []

    # Check basic structure
    if not lines or not lines[0].startswith('# '):
        errors.append("Missing H1 header")

    h1_count = sum(1 for line in lines if line.startswith('# '))
    if h1_count > 1:
        errors.append("Multiple H1 headers")

    # Validate links
    links = re.findall(r'\[([^\]]+)\]\(([^\)]+)\)', content)
    for _, url in links:
        if not url.startswith(('http://', 'https://', '/')):
            errors.append(f"Invalid URL: {url}")

    if errors:
        print("Validation failed:")
        for error in errors:
            print(f"  - {error}")
        return False

    sections = sum(1 for line in lines if line.startswith('## '))
    print(f"Valid llms.txt: {sections} sections, {len(links)} links")
    return True


if __name__ == "__main__":
    build_path = Path(__file__).parent / "build" / "html" / "llms.txt"
    source_path = Path(__file__).parent / "source" / "llms.txt"

    path = build_path if build_path.exists() else source_path
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)

    sys.exit(0 if validate_llms_txt(path) else 1)

