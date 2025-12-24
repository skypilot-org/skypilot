"""Export markdown versions of documentation pages for LLM consumption."""

from pathlib import Path
import re

from sphinx.application import Sphinx


def export_markdown_files(app, exception):
    if exception:
        return

    for docname in app.env.found_docs:
        # Skip temporary files
        if '.temp' in docname or docname.startswith('_'):
            continue

        html_path = Path(app.outdir) / f"{docname}.html"
        if not html_path.exists():
            continue

        source_path = Path(app.srcdir) / app.env.doc2path(docname, base=False)
        md_path = Path(app.outdir) / f"{docname}.html.md"

        if source_path.suffix in ['.md', '.rst']:
            try:
                process_source_file(source_path, md_path)
            except Exception as e:
                print(f"Warning: Failed to process {source_path}: {e}")


def process_source_file(source_path, md_path):
    md_path.parent.mkdir(parents=True, exist_ok=True)
    content = source_path.read_text(encoding='utf-8')

    if source_path.suffix == '.md':
        # Resolve MyST includes
        content = resolve_includes(content, source_path,
                                   r':::\{include\} ([^\n]+)\n:::')
        content = resolve_literalincludes(
            content, source_path,
            r':::\{literalinclude\} ([^\n]+)\n(?::language: (\w+)\n)?:::')
        # Convert MyST admonitions to headers
        content = re.sub(
            r':::\{admonition\} ([^\n]+)\n:class: dropdown\n\n(.*?)\n:::\n:::',
            r'## \1\n\n\2',
            content,
            flags=re.DOTALL)
    elif source_path.suffix == '.rst':
        try:
            import pypandoc

            # Resolve RST includes
            content = resolve_literalincludes(
                content, source_path,
                r'\.\. literalinclude:: ([^\n]+)\n(?:   :language: (\w+)\n)?(?:   :[^\n]*\n)*'
            )

            # Pre-process emoji substitutions to avoid pandoc reference errors
            # Convert RST emoji syntax |:emoji:| to Unicode characters
            emoji_map = {
                '|:yellow_circle:|': '🟡',
                '|:white_check_mark:|': '✅',
                '|:x:|': '❌',
                '|:tada:|': '🎉'
            }
            for rst_emoji, unicode_emoji in emoji_map.items():
                content = content.replace(rst_emoji, unicode_emoji)

            # Fix pandoc reference errors for 'set_' in code blocks
            # Pandoc interprets 'set_' as a reference to 'set' We escape it in
            # code blocks to prevent this.
            def escape_set_in_code_blocks(text):
                # Split by code block markers
                parts = re.split(r'(```[^\n]*\n.*?```)', text, flags=re.DOTALL)
                result = []
                for i, part in enumerate(parts):
                    # Odd indices are code blocks
                    if i % 2 == 1:
                        # Escape set_ in code blocks
                        part = part.replace('set_=', 'set\\_=')
                    result.append(part)
                return ''.join(result)

            content = escape_set_in_code_blocks(content)

            # Convert via pandoc
            temp_rst = md_path.with_suffix('.temp.rst')
            temp_rst.write_text(content, encoding='utf-8')
            try:
                pypandoc.convert_file(
                    str(temp_rst),
                    'md',
                    outputfile=str(md_path),
                    extra_args=['--wrap=none', '--from=rst', '--to=gfm'])
            except Exception as e:
                # If pandoc fails, copy raw RST content as fallback
                print(
                    f"Warning: Pandoc conversion failed for {source_path}: {e}")
                md_path.write_text(content, encoding='utf-8')
            finally:
                if temp_rst.exists():
                    temp_rst.unlink()
            return
        except ImportError:
            pass  # Fall back to copying raw content

    md_path.write_text(content, encoding='utf-8')


def resolve_includes(content, source_path, pattern):

    def replace_include(match):
        include_path = source_path.parent / match.group(1)
        return include_path.read_text() if include_path.exists(
        ) else match.group(0)

    return re.sub(pattern, replace_include, content)


def resolve_literalincludes(content, source_path, pattern):

    def replace_literalinclude(match):
        include_path = source_path.parent / match.group(1)
        language = match.group(
            2) if len(match.groups()) > 1 and match.group(2) else 'yaml'
        if include_path.exists():
            return f"```{language}\n{include_path.read_text()}\n```"
        return match.group(0)

    return re.sub(pattern, replace_literalinclude, content)


def setup(app: Sphinx):
    app.connect('build-finished', export_markdown_files)
    return {'version': '1.0', 'parallel_read_safe': True}
