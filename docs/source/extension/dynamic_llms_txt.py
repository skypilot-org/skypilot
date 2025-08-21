"""Generate llms.txt with environment-appropriate URLs."""

import os
from pathlib import Path
from sphinx.application import Sphinx
from sphinx.util import logging

logger = logging.getLogger(__name__)


def get_base_url():
    """Get base URL based on build environment."""
    if os.getenv('SPHINX_BUILD_PRODUCTION') == 'true':
        return "https://docs.skypilot.co/en/latest"
    
    port = os.getenv('SPHINX_PORT', os.getenv('PORT', '8000'))
    return f"http://127.0.0.1:{port}"


def generate_llms_txt(app: Sphinx, exception: Exception) -> None:
    """Generate llms.txt after build completes."""
    if exception:
        return
    
    template_path = Path(app.srcdir) / "llms.txt.template"
    if not template_path.exists():
        return
    
    base_url = get_base_url()
    template_content = template_path.read_text()
    llms_content = template_content.replace('{{BASE_URL}}', base_url)
    
    # Write to build output
    output_path = Path(app.outdir) / "llms.txt"
    output_path.write_text(llms_content)
    
    # Update source copy
    source_path = Path(app.srcdir) / "llms.txt"
    source_path.write_text(llms_content)


def setup(app: Sphinx):
    app.connect('build-finished', generate_llms_txt)
    return {'version': '1.0', 'parallel_read_safe': True, 'parallel_write_safe': True}