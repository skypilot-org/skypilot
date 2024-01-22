# Configuration file for the Sphinx documentation builder.

import os
import sys

sys.path.insert(0, os.path.abspath('.'))
sys.path.insert(0, os.path.abspath('../'))
sys.path.insert(0, os.path.abspath('../../'))

# -- Project information

project = 'SkyPilot'
copyright = '2023, SkyPilot Team'
author = 'the SkyPilot authors'

# The version info for the project you're documenting, acts as replacement for
# |version| and |release|, also used in various other places throughout the
# built documents.
from sky import __version__ as version

# The full version, including alpha/beta/rc tags.
release = version

# -- General configuration

extensions = [
    'sphinxemoji.sphinxemoji',
    'sphinx.ext.autodoc',
    'sphinx.ext.autosummary',
    'sphinx.ext.duration',
    'sphinx.ext.doctest',
    'sphinx.ext.intersphinx',
    'sphinx.ext.napoleon',
    'sphinx.ext.viewcode',
    'sphinx_autodoc_typehints',
    'sphinx_click',
    'sphinx_copybutton',
    'sphinx_design',
    'myst_parser',
]

intersphinx_mapping = {
    'python': ('https://docs.python.org/3/', None),
    'sphinx': ('https://www.sphinx-doc.org/en/master/', None),
}
intersphinx_disabled_domains = ['std']

templates_path = ['_templates']

# The main toctree document.
main_doc = 'index'

pygments_style = None
autosummary_generate = True
napolean_use_rtype = False

# -- Options for autodoc

# Python methods should be presented in source code order
autodoc_member_order = 'bysource'

# -- Options for HTML output

html_theme = 'sphinx_book_theme'
html_theme_options = {
    # 'show_toc_level': 2,
    'logo_only': True,
    'repository_url': 'https://github.com/skypilot-org/skypilot',
    'use_repository_button': True,
    'use_issues_button': True,
    'use_edit_page_button': True,
    'path_to_docs': 'docs/source',
}

# The name for this set of Sphinx documents.  If None, it defaults to
# "<project> v<release> documentation".
html_title = 'SkyPilot documentation'

# -- Options for EPUB output
epub_show_urls = 'footnote'

# -- Options for sphinx-copybutton
copybutton_prompt_text = r'\$ '
copybutton_prompt_is_regexp = True

html_show_sourcelink = False

# The name of an image file (relative to this directory) to place at the top
# of the sidebar.
html_logo = 'images/skypilot-wide-light-1k.png'

# The name of an image file (within the static path) to use as favicon of the
# docs. This file should be a Windows icon file (.ico), 16x16 or 32x32 pixels.
html_favicon = '_static/favicon.ico'

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named 'default.css' will overwrite the builtin 'default.css'.
html_static_path = ['_static']
html_js_files = ["custom.js"]
html_css_files = ["custom.css"]
