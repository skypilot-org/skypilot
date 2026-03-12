#!/usr/bin/env python3
"""Generate reference documentation for the SkyPilot skill.

Generates three reference files from source:
  - references/cli-reference.md: From Click command definitions
  - references/yaml-spec.md: From docs/source/reference/yaml-spec.rst
  - references/python-sdk.md: From sky/client/sdk.py docstrings

Usage:
    python agent/scripts/generate_references.py

Run from the SkyPilot repository root.
"""

import ast
import os
import re
import sys
import textwrap


def _get_repo_root():
    """Get the repository root directory."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # scripts/ -> agent/ -> repo root
    return os.path.normpath(os.path.join(script_dir, '..', '..'))


REPO_ROOT = _get_repo_root()
REFERENCES_DIR = os.path.join(REPO_ROOT, 'agent', 'skills', 'skypilot',
                              'references')

# ---------------------------------------------------------------------------
# YAML Spec Generation
# ---------------------------------------------------------------------------


def _rst_to_markdown(rst_text: str) -> str:
    """Convert RST content to Markdown, stripping Sphinx directives."""
    lines = rst_text.split('\n')
    out = []
    i = 0
    in_code_block = False
    code_block_indent = 0
    in_parsed_literal = False
    parsed_literal_indent = 0
    in_directive_body = False
    directive_indent = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Handle code-block directive
        code_block_match = re.match(r'^(\s*)\.\.  ?code-block::\s*(\w+)?', line)
        if code_block_match:
            indent = len(code_block_match.group(1))
            lang = code_block_match.group(2) or ''
            out.append(f'```{lang}')
            in_code_block = True
            code_block_indent = indent + 2
            i += 1
            # Skip blank line after directive
            if i < len(lines) and lines[i].strip() == '':
                i += 1
            continue

        if in_code_block:
            if stripped == '' and i + 1 < len(lines):
                next_line = lines[i + 1]
                next_stripped = next_line.strip()
                # End code block if next non-empty line is not indented enough
                if next_stripped and not next_line.startswith(
                        ' ' * code_block_indent):
                    out.append('```')
                    out.append('')
                    in_code_block = False
                    i += 1
                    continue
                else:
                    out.append(line[code_block_indent:] if line.
                               startswith(' ' *
                                          code_block_indent) else line.strip())
                    i += 1
                    continue
            elif stripped == '' and i + 1 >= len(lines):
                out.append('```')
                in_code_block = False
                i += 1
                continue
            elif not line.startswith(
                    ' ' * code_block_indent) and stripped != '':
                out.append('```')
                out.append('')
                in_code_block = False
                continue
            else:
                content = line[code_block_indent:] if line.startswith(
                    ' ' * code_block_indent) else line.strip()
                out.append(content)
                i += 1
                continue

        # Handle parsed-literal directive (convert to code block)
        if re.match(r'^(\s*)\.\.  ?parsed-literal::', line):
            match = re.match(r'^(\s*)\.\.  ?parsed-literal::', line)
            parsed_literal_indent = len(match.group(1)) + 2
            in_parsed_literal = True
            out.append('```yaml')
            i += 1
            # Skip blank line
            if i < len(lines) and lines[i].strip() == '':
                i += 1
            continue

        if in_parsed_literal:
            if stripped == '' and i + 1 < len(lines):
                next_line = lines[i + 1]
                next_stripped = next_line.strip()
                if next_stripped and not next_line.startswith(
                        ' ' * parsed_literal_indent):
                    out.append('```')
                    out.append('')
                    in_parsed_literal = False
                    i += 1
                    continue
                else:
                    content = line[parsed_literal_indent:] if line.startswith(
                        ' ' * parsed_literal_indent) else ''
                    # Strip :ref: links in parsed literals - keep display text
                    content = re.sub(r':ref:`([^<]+?)\s*<[^>]+>`', r'\1',
                                     content)
                    content = re.sub(r':ref:`([^`]+)`', r'\1', content)
                    out.append(content)
                    i += 1
                    continue
            elif stripped == '' and i + 1 >= len(lines):
                out.append('```')
                in_parsed_literal = False
                i += 1
                continue
            elif not line.startswith(
                    ' ' * parsed_literal_indent) and stripped != '':
                out.append('```')
                out.append('')
                in_parsed_literal = False
                continue
            else:
                content = line[parsed_literal_indent:] if line.startswith(
                    ' ' * parsed_literal_indent) else line.strip()
                # Strip :ref: links - keep display text
                content = re.sub(r':ref:`([^<]+?)\s*<[^>]+>`', r'\1', content)
                content = re.sub(r':ref:`([^`]+)`', r'\1', content)
                out.append(content)
                i += 1
                continue

        # Skip anchor definitions (.. _name:)
        if re.match(r'^\s*\.\. _[\w-]+:\s*$', line):
            i += 1
            continue

        # Handle warning/note directives -> blockquote
        warning_match = re.match(
            r'^(\s*)\.\. (warning|note|tip|important)::\s*(.*)', line)
        if warning_match:
            indent = len(warning_match.group(1))
            dtype = warning_match.group(2).upper()
            first_line = warning_match.group(3).strip()
            directive_indent = indent + 3
            if first_line:
                out.append(f'> **{dtype}**: {first_line}')
            else:
                out.append(f'> **{dtype}**:')
            in_directive_body = True
            i += 1
            continue

        if in_directive_body:
            if stripped == '':
                # Could be paragraph break within directive or end
                if i + 1 < len(lines) and lines[i + 1].startswith(
                        ' ' * directive_indent):
                    out.append('>')
                    i += 1
                    continue
                else:
                    out.append('')
                    in_directive_body = False
                    i += 1
                    continue
            elif line.startswith(' ' * directive_indent):
                content = line[directive_indent:].rstrip()
                out.append(f'> {content}')
                i += 1
                continue
            else:
                out.append('')
                in_directive_body = False
                # Don't increment - reprocess this line
                continue

        # Skip other directives we don't handle (.. tab-set::, etc.)
        if re.match(r'^\s*\.\. [\w-]+::', line) and not stripped.startswith(
                '.. code-block') and not stripped.startswith(
                    '.. parsed-literal'):
            i += 1
            # Skip directive body
            while i < len(lines):
                if lines[i].strip() == '':
                    i += 1
                    continue
                if lines[i].startswith('   '):
                    i += 1
                    continue
                break
            continue

        # Convert RST headings
        # Check for underline pattern on next line
        if i + 1 < len(lines) and stripped and len(stripped) > 0:
            next_stripped = lines[i + 1].strip() if i + 1 < len(lines) else ''
            if next_stripped and len(next_stripped) >= len(
                    stripped) and re.match(r'^[=\-~`]+$', next_stripped):
                char = next_stripped[0]
                if char == '=':
                    out.append(f'# {stripped}')
                elif char == '-':
                    out.append(f'## {stripped}')
                elif char == '~':
                    out.append(f'### {stripped}')
                elif char == '`':
                    out.append(f'#### {stripped}')
                i += 2  # Skip the underline
                continue

        # Convert inline RST markup
        converted = line
        # :ref:`text <target>` -> text (keep display text, drop anchor)
        converted = re.sub(r':ref:`([^<]+?)\s*<[^>]+>`', r'\1', converted)
        # :ref:`target` -> target
        converted = re.sub(r':ref:`([^`]+)`', r'\1', converted)
        # :meth:`sky.Foo.bar` -> `sky.Foo.bar()`
        converted = re.sub(r':meth:`([^`]+)`', r'`\1()`', converted)
        # :class:`sky.Foo` -> `sky.Foo`
        converted = re.sub(r':class:`([^`]+)`', r'`\1`', converted)
        # :code:`text` -> `text`
        converted = re.sub(r':code:`([^`]+)`', r'`\1`', converted)
        # :py:obj:`text` -> `text`
        converted = re.sub(r':py:\w+:`([^`]+)`', r'`\1`', converted)
        # ``literal`` -> `literal`
        converted = re.sub(r'``([^`]+)``', r'`\1`', converted)

        out.append(converted)
        i += 1

    # Close any unclosed blocks
    if in_code_block or in_parsed_literal:
        out.append('```')

    return '\n'.join(out)


def generate_yaml_spec():
    """Read yaml-spec.rst, convert to markdown, write to references/."""
    rst_path = os.path.join(REPO_ROOT, 'docs', 'source', 'reference',
                            'yaml-spec.rst')
    if not os.path.exists(rst_path):
        print(f'ERROR: {rst_path} not found')
        sys.exit(1)

    with open(rst_path, 'r') as f:
        rst_content = f.read()

    md_content = _rst_to_markdown(rst_content)

    header = ('<!-- AUTO-GENERATED from docs/source/reference/yaml-spec.rst '
              '-->\n'
              '<!-- Run: python skills/skypilot/scripts/'
              'generate_references.py -->\n\n')
    output_path = os.path.join(REFERENCES_DIR, 'yaml-spec.md')
    full_content = (header + md_content).rstrip('\n') + '\n'
    with open(output_path, 'w') as f:
        f.write(full_content)

    line_count = full_content.count('\n')
    print(f'Generated {output_path} ({line_count} lines)')


# ---------------------------------------------------------------------------
# CLI Reference Generation
# ---------------------------------------------------------------------------


def _format_option(param) -> str:
    """Format a Click option/argument as markdown."""
    import click as click_module
    if isinstance(param, click_module.Argument):
        return f'- `{param.human_readable_name}` — {param.type.name}'

    # It's an Option
    opts = ', '.join(f'`{o}`' for o in param.opts)
    if param.secondary_opts:
        opts += ', ' + ', '.join(f'`{o}`' for o in param.secondary_opts)

    help_text = param.help or ''
    # Clean up help text
    help_text = help_text.replace('\n', ' ').strip()
    if len(help_text) > 200:
        help_text = help_text[:200] + '...'

    parts = [f'- {opts}']
    if param.default is not None and param.default != () and not param.is_flag:
        parts.append(f'(default: `{param.default}`)')
    if help_text:
        parts.append(f'— {help_text}')

    return ' '.join(parts)


def _format_command(name: str, cmd, prefix: str = 'sky') -> str:
    """Format a Click command as markdown."""
    full_name = f'{prefix} {name}'
    lines = [f'### `{full_name}`\n']

    # Help text
    help_text = cmd.help or ''
    if help_text:
        # Take first paragraph
        paragraphs = help_text.strip().split('\n\n')
        first_para = paragraphs[0].replace('\n', ' ').strip()
        lines.append(first_para)
        lines.append('')

    # Options
    params = [p for p in cmd.params if p.name != 'help']
    if params:
        lines.append('**Options:**\n')
        for param in params:
            lines.append(_format_option(param))
        lines.append('')

    return '\n'.join(lines)


def generate_cli_reference():
    """Import Click commands and generate CLI reference markdown."""
    # Add repo root to path so we can import sky
    sys.path.insert(0, REPO_ROOT)

    # Set environment to avoid server startup
    os.environ['SKYPILOT_DEV'] = '1'

    try:
        from sky.client.cli.command import cli as cli_group
    except Exception as e:
        print(f'ERROR: Could not import CLI commands: {e}')
        print('Make sure SkyPilot is installed: pip install -e ".[all]"')
        sys.exit(1)

    lines = [
        '<!-- AUTO-GENERATED from sky/client/cli/command.py -->',
        '<!-- Run: python agent/scripts/generate_references.py -->',
        '',
        '# SkyPilot CLI Reference',
        '',
        'Complete reference for all `sky` commands and options.',
        '',
    ]

    # Categorize commands
    groups = {}
    standalone = {}

    import click as click_module
    for name, cmd in sorted(cli_group.commands.items()):
        if getattr(cmd, 'hidden', False):
            continue
        if isinstance(cmd, click_module.Group):
            groups[name] = cmd
        else:
            standalone[name] = cmd

    # Core cluster commands
    cluster_cmds = [
        'launch', 'exec', 'status', 'stop', 'start', 'down', 'autostop',
        'cost-report'
    ]
    lines.append('## Core Cluster Commands\n')
    for name in cluster_cmds:
        if name in standalone:
            lines.append(_format_command(name, standalone[name]))

    # Job queue commands
    job_cmds = ['queue', 'logs', 'cancel']
    lines.append('## Job Queue Commands\n')
    for name in job_cmds:
        if name in standalone:
            lines.append(_format_command(name, standalone[name]))

    # Infrastructure commands
    infra_cmds = ['check', 'show-gpus']
    lines.append('## Infrastructure Commands\n')
    for name in infra_cmds:
        if name in standalone:
            lines.append(_format_command(name, standalone[name]))

    # Dashboard
    if 'dashboard' in standalone:
        lines.append('## Dashboard\n')
        lines.append(_format_command('dashboard', standalone['dashboard']))

    # Command groups
    group_sections = {
        'gpus': 'GPU/Accelerator Commands',
        'jobs': 'Managed Jobs Commands',
        'serve': 'SkyServe Commands',
        'storage': 'Storage Commands',
        'volumes': 'Volume Commands',
        'api': 'API Server Commands',
        'ssh': 'SSH Node Pool Commands',
    }

    for group_name, section_title in group_sections.items():
        if group_name not in groups:
            continue
        group = groups[group_name]
        lines.append(f'## {section_title}\n')

        # Group description
        if group.help:
            first_para = group.help.strip().split('\n\n')[0].replace('\n', ' ')
            lines.append(first_para)
            lines.append('')

        for sub_name, sub_cmd in sorted(group.commands.items()):
            if getattr(sub_cmd, 'hidden', False):
                continue
            if isinstance(sub_cmd, click_module.Group):
                # Nested group (e.g., jobs pool)
                lines.append(f'### `sky {group_name} {sub_name}` (subgroup)\n')
                if sub_cmd.help:
                    lines.append(sub_cmd.help.strip().split('\n\n')[0].replace(
                        '\n', ' '))
                    lines.append('')
                for nested_name, nested_cmd in sorted(sub_cmd.commands.items()):
                    if getattr(nested_cmd, 'hidden', False):
                        continue
                    lines.append(
                        _format_command(
                            f'{group_name} {sub_name} {nested_name}',
                            nested_cmd))
            else:
                lines.append(
                    _format_command(f'{group_name} {sub_name}', sub_cmd))

    # Any remaining standalone commands not yet listed
    listed = set(cluster_cmds + job_cmds + infra_cmds + ['dashboard'])
    remaining = {k: v for k, v in standalone.items() if k not in listed}
    if remaining:
        lines.append('## Other Commands\n')
        for name in sorted(remaining):
            lines.append(_format_command(name, remaining[name]))

    output_path = os.path.join(REFERENCES_DIR, 'cli-reference.md')
    content = '\n'.join(lines).rstrip('\n') + '\n'
    with open(output_path, 'w') as f:
        f.write(content)

    line_count = content.count('\n')
    print(f'Generated {output_path} ({line_count} lines)')


# ---------------------------------------------------------------------------
# Python SDK Generation
# ---------------------------------------------------------------------------


def _format_signature(node: ast.FunctionDef) -> str:
    """Format a function signature from AST node."""
    args = node.args
    parts = []

    # Regular args
    num_defaults = len(args.defaults)
    num_args = len(args.args)
    for idx, arg in enumerate(args.args):
        if arg.arg == 'self':
            continue
        annotation = ''
        if arg.annotation:
            annotation = f': {ast.unparse(arg.annotation)}'

        # Check if this arg has a default
        default_idx = idx - (num_args - num_defaults)
        if default_idx >= 0:
            default_val = ast.unparse(args.defaults[default_idx])
            parts.append(f'{arg.arg}{annotation} = {default_val}')
        else:
            parts.append(f'{arg.arg}{annotation}')

    # *args
    if args.vararg:
        annotation = ''
        if args.vararg.annotation:
            annotation = f': {ast.unparse(args.vararg.annotation)}'
        parts.append(f'*{args.vararg.arg}{annotation}')
    elif args.kwonlyargs:
        parts.append('*')

    # keyword-only args
    for idx, arg in enumerate(args.kwonlyargs):
        annotation = ''
        if arg.annotation:
            annotation = f': {ast.unparse(arg.annotation)}'
        if idx < len(args.kw_defaults) and args.kw_defaults[idx] is not None:
            default_val = ast.unparse(args.kw_defaults[idx])
            parts.append(f'{arg.arg}{annotation} = {default_val}')
        else:
            parts.append(f'{arg.arg}{annotation}')

    # **kwargs
    if args.kwarg:
        annotation = ''
        if args.kwarg.annotation:
            annotation = f': {ast.unparse(args.kwarg.annotation)}'
        parts.append(f'**{args.kwarg.arg}{annotation}')

    sig = ', '.join(parts)

    # Return annotation
    returns = ''
    if node.returns:
        returns = f' -> {ast.unparse(node.returns)}'

    return f'{node.name}({sig}){returns}'


def _format_docstring(docstring: str) -> str:
    """Clean up a docstring for markdown output."""
    if not docstring:
        return ''
    # Dedent
    lines = docstring.split('\n')
    if len(lines) > 1:
        docstring = lines[0] + '\n' + textwrap.dedent('\n'.join(lines[1:]))
    docstring = docstring.strip()

    # Convert RST code blocks to markdown
    docstring = re.sub(r'\.\. code-block:: (\w+)', r'```\1', docstring)
    # Convert field lists (Args:, Returns:, etc.) to bold headers
    docstring = re.sub(r'^(\w[\w ]+):$',
                       r'**\1:**',
                       docstring,
                       flags=re.MULTILINE)

    return docstring


def generate_python_sdk():
    """Parse sdk.py with ast and generate SDK reference markdown."""
    sdk_path = os.path.join(REPO_ROOT, 'sky', 'client', 'sdk.py')
    if not os.path.exists(sdk_path):
        print(f'ERROR: {sdk_path} not found')
        sys.exit(1)

    with open(sdk_path, 'r') as f:
        source = f.read()

    tree = ast.parse(source)

    # Extract module docstring
    module_doc = ast.get_docstring(tree) or ''

    # Collect public functions (not starting with _)
    functions = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith('_'):
                docstring = ast.get_docstring(node) or ''
                sig = _format_signature(node)
                functions.append((node.name, sig, docstring, node.lineno))

    # Categorize functions
    categories = {
        'Cluster Operations': [
            'launch',
            'exec',
            'stop',
            'start',
            'down',
            'status',
            'autostop',
            'cost_report',
            'endpoints',
        ],
        'Job Operations': [
            'queue',
            'job_status',
            'cancel',
            'tail_logs',
            'tail_provision_logs',
            'tail_autostop_logs',
            'download_logs',
        ],
        'Managed Jobs': [
            'jobs_launch',
            'jobs_queue',
            'jobs_cancel',
            'jobs_tail_logs',
            'jobs_dashboard',
        ],
        'SkyServe': [
            'serve_up',
            'serve_update',
            'serve_status',
            'serve_down',
            'serve_tail_logs',
        ],
        'Infrastructure & Configuration': [
            'check',
            'enabled_clouds',
            'list_accelerators',
            'kubernetes_node_info',
            'realtime_kubernetes_gpu_availability',
            'optimize',
            'validate',
            'reload_config',
        ],
        'Storage & Volumes': [
            'storage_ls',
            'storage_delete',
            'volumes_apply',
            'volumes_ls',
            'volumes_delete',
        ],
        'API Server': [
            'api_start',
            'api_stop',
            'api_status',
            'api_info',
            'api_cancel',
            'api_server_logs',
            'api_login',
            'api_logout',
        ],
        'Utilities': [
            'dashboard',
            'get',
            'stream_and_get',
            'api_get',
            'workspaces',
        ],
    }

    # Build name -> (sig, docstring, lineno) map
    func_map = {
        name: (sig, doc, lineno) for name, sig, doc, lineno in functions
    }

    lines = [
        '<!-- AUTO-GENERATED from sky/client/sdk.py -->',
        '<!-- Run: python agent/scripts/generate_references.py -->',
        '',
        '# SkyPilot Python SDK Reference',
        '',
        'All SDK functions return a request ID (future). Use `sky.get(request_id)` '
        'to await the result.',
        '',
        '```python',
        'import sky',
        '',
        'request_id = sky.launch(sky.Task.from_yaml("task.yaml"))',
        'result = sky.get(request_id)',
        '```',
        '',
    ]

    categorized = set()
    for category, func_names in categories.items():
        # Only include category if it has functions
        cat_funcs = [(n, func_map[n]) for n in func_names if n in func_map]
        if not cat_funcs:
            continue

        lines.append(f'## {category}\n')
        for name, (sig, docstring, lineno) in cat_funcs:
            categorized.add(name)
            lines.append(f'### `sky.{name}`\n')
            lines.append(f'```python')
            # Wrap long signatures
            if len(sig) > 100:
                lines.append(f'sky.{sig}')
            else:
                lines.append(f'sky.{sig}')
            lines.append('```\n')

            if docstring:
                formatted = _format_docstring(docstring)
                # Only include up to "Args:" section for brevity, but
                # include the full docstring since it's the authoritative source
                lines.append(formatted)
                lines.append('')

    # Uncategorized functions
    uncategorized = [
        (name, func_map[name]) for name in func_map if name not in categorized
    ]
    if uncategorized:
        lines.append('## Other Functions\n')
        for name, (sig, docstring, lineno) in sorted(uncategorized):
            lines.append(f'### `sky.{name}`\n')
            lines.append(f'```python\nsky.{sig}\n```\n')
            if docstring:
                formatted = _format_docstring(docstring)
                lines.append(formatted)
                lines.append('')

    output_path = os.path.join(REFERENCES_DIR, 'python-sdk.md')
    content = '\n'.join(lines).rstrip('\n') + '\n'
    with open(output_path, 'w') as f:
        f.write(content)

    line_count = content.count('\n')
    print(f'Generated {output_path} ({line_count} lines)')
    print(f'  Total public functions: {len(func_map)}')
    print(f'  Categorized: {len(categorized)}, '
          f'Uncategorized: {len(func_map) - len(categorized)}')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    os.makedirs(REFERENCES_DIR, exist_ok=True)

    print('Generating YAML spec reference...')
    generate_yaml_spec()
    print()

    print('Generating CLI reference...')
    generate_cli_reference()
    print()

    print('Generating Python SDK reference...')
    generate_python_sdk()
    print()

    print('Done! All references generated in agent/skills/skypilot/references/')


if __name__ == '__main__':
    main()
