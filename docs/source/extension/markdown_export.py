"""Export markdown versions of documentation pages for LLM consumption."""

import re
from pathlib import Path
from sphinx.application import Sphinx


def export_markdown_files(app, exception):
    if exception:
        return
    
    for docname in app.env.found_docs:
        html_path = Path(app.outdir) / f"{docname}.html"
        if not html_path.exists():
            continue
            
        source_path = Path(app.srcdir) / app.env.doc2path(docname, base=False)
        md_path = Path(app.outdir) / f"{docname}.html.md"
        
        if source_path.suffix in ['.md', '.rst']:
            process_source_file(source_path, md_path)


def process_source_file(source_path, md_path):
    md_path.parent.mkdir(parents=True, exist_ok=True)
    content = source_path.read_text(encoding='utf-8')
    
    if source_path.suffix == '.md':
        # Resolve MyST includes
        content = resolve_includes(content, source_path, r':::\{include\} ([^\n]+)\n:::')
        content = resolve_literalincludes(content, source_path, r':::\{literalinclude\} ([^\n]+)\n(?::language: (\w+)\n)?:::')
        # Convert MyST admonitions to headers
        content = re.sub(r':::\{admonition\} ([^\n]+)\n:class: dropdown\n\n(.*?)\n:::\n:::', r'## \1\n\n\2', content, flags=re.DOTALL)
    elif source_path.suffix == '.rst':
        try:
            import pypandoc
            # Resolve RST includes
            content = resolve_literalincludes(content, source_path, r'\.\. literalinclude:: ([^\n]+)\n(?:   :language: (\w+)\n)?(?:   :[^\n]*\n)*')
            # Convert via pandoc
            temp_rst = md_path.with_suffix('.temp.rst')
            temp_rst.write_text(content, encoding='utf-8')
            pypandoc.convert_file(str(temp_rst), 'md', outputfile=str(md_path), extra_args=['--wrap=none', '--from=rst', '--to=gfm'])
            temp_rst.unlink()
            return
        except ImportError:
            pass  # Fall back to copying raw content
    
    md_path.write_text(content, encoding='utf-8')


def resolve_includes(content, source_path, pattern):
    def replace_include(match):
        include_path = source_path.parent / match.group(1)
        return include_path.read_text() if include_path.exists() else match.group(0)
    return re.sub(pattern, replace_include, content)


def resolve_literalincludes(content, source_path, pattern):
    def replace_literalinclude(match):
        include_path = source_path.parent / match.group(1)
        language = match.group(2) if len(match.groups()) > 1 and match.group(2) else 'yaml'
        if include_path.exists():
            return f"```{language}\n{include_path.read_text()}\n```"
        return match.group(0)
    return re.sub(pattern, replace_literalinclude, content)


def setup(app: Sphinx):
    app.connect('build-finished', export_markdown_files)
    return {'version': '1.0', 'parallel_read_safe': True}