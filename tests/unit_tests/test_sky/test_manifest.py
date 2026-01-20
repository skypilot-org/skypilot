"""Tests for MANIFEST.in completeness.

Ensures that shell scripts referenced by other scripts are included in the
package distribution.
"""
import fnmatch
import os
import re

import pytest

# Root directory of the skypilot package (tests/unit_tests/test_sky -> root)
ROOT_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
MANIFEST_PATH = os.path.join(ROOT_DIR, 'sky', 'setup_files', 'MANIFEST.in')


def parse_manifest_patterns():
    """Parse MANIFEST.in and return list of include patterns."""
    patterns = []
    with open(MANIFEST_PATH, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            # Handle 'include' and 'recursive-include' directives
            if line.startswith('include '):
                pattern = line[len('include '):]
                patterns.append(pattern)
            elif line.startswith('recursive-include '):
                # Format: recursive-include <dir> <pattern>
                parts = line[len('recursive-include '):].split(None, 1)
                if len(parts) == 2:
                    directory, file_pattern = parts
                    # Convert to glob pattern
                    patterns.append(os.path.join(directory, '**', file_pattern))
    return patterns


def is_path_included(path, patterns):
    """Check if a path matches any of the MANIFEST.in patterns."""
    # Normalize path separators
    path = path.replace(os.sep, '/')
    for pattern in patterns:
        pattern = pattern.replace(os.sep, '/')
        # Handle glob patterns
        if fnmatch.fnmatch(path, pattern):
            return True
        # Handle directory/* patterns
        if pattern.endswith('/*'):
            dir_pattern = pattern[:-2]
            path_dir = os.path.dirname(path)
            if path_dir == dir_pattern or fnmatch.fnmatch(
                    path_dir, dir_pattern):
                return True
        # Handle recursive patterns with **
        if '**' in pattern:
            # Convert ** pattern to regex
            regex_pattern = pattern.replace('**/', '(.*/)?')
            regex_pattern = regex_pattern.replace('*', '[^/]*')
            regex_pattern = f'^{regex_pattern}$'
            if re.match(regex_pattern, path):
                return True
    return False


def find_script_references(script_path):
    """Find paths to other scripts referenced via exec or source."""
    references = []
    try:
        with open(script_path, 'r') as f:
            content = f.read()
    except (IOError, UnicodeDecodeError):
        return references

    # Match patterns like: exec "$SCRIPT_DIR/../../path/to/script.sh"
    # or: source "$SCRIPT_DIR/../other_script.sh"
    exec_pattern = r'exec\s+["\']?\$\{?SCRIPT_DIR\}?([^"\']+\.sh)["\']?'
    source_pattern = (r'(?:source|\.)[ \t]+["\']?\$\{?SCRIPT_DIR\}?'
                      r'([^"\']+\.sh)["\']?')

    for pattern in [exec_pattern, source_pattern]:
        for match in re.finditer(pattern, content):
            relative_path = match.group(1)
            # Resolve the relative path from the script's directory
            script_dir = os.path.dirname(script_path)
            absolute_path = os.path.normpath(
                os.path.join(script_dir, relative_path.lstrip('/')))
            if os.path.exists(absolute_path):
                references.append(absolute_path)

    return references


def get_all_shell_scripts():
    """Get all shell scripts in the sky directory."""
    sky_dir = os.path.join(ROOT_DIR, 'sky')
    scripts = []
    for root, _, files in os.walk(sky_dir):
        for filename in files:
            if filename.endswith('.sh'):
                scripts.append(os.path.join(root, filename))
    return scripts


def test_shell_script_references_included():
    """Verify that shell scripts referenced by other scripts are included.

    This prevents issues like the ssh-tunnel.sh redirect stub referencing
    a script that isn't included in the package distribution.
    """
    patterns = parse_manifest_patterns()
    scripts = get_all_shell_scripts()

    missing_references = []
    for script in scripts:
        # Get the relative path from ROOT_DIR
        rel_script_path = os.path.relpath(script, ROOT_DIR)

        # Check if this script is included in MANIFEST.in
        if not is_path_included(rel_script_path, patterns):
            # Script not included, skip checking its references
            continue

        # Find references to other scripts
        references = find_script_references(script)
        for ref in references:
            rel_ref_path = os.path.relpath(ref, ROOT_DIR)
            if not is_path_included(rel_ref_path, patterns):
                missing_references.append({
                    'source': rel_script_path,
                    'referenced': rel_ref_path
                })

    if missing_references:
        msg_parts = [
            'The following scripts are referenced but not included in '
            'MANIFEST.in:'
        ]
        for ref in missing_references:
            msg_parts.append(
                f"  - {ref['referenced']} (referenced by {ref['source']})")
        msg_parts.append(
            '\nPlease add the missing paths to sky/setup_files/MANIFEST.in')
        pytest.fail('\n'.join(msg_parts))
