# Configuration file for the Sphinx documentation builder.

import sys
import os

sys.path.insert(0, os.path.abspath('.'))
sys.path.insert(0, os.path.abspath('../'))
sys.path.insert(0, os.path.abspath('../../'))

# -- Project information

project = 'SkyPilot'
copyright = '2022, SkyPilot Team'
author = 'the SkyPilot authors'

release = '0.1'
version = '0.1.0'

# -- General configuration

extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.autosectionlabel',
    'sphinx.ext.autosummary',
    'sphinx.ext.duration',
    'sphinx.ext.doctest',
    'sphinx.ext.intersphinx',
    'sphinx.ext.napoleon',
    'sphinx.ext.viewcode',
    'sphinx_autodoc_typehints',
    'sphinx_click',
    'sphinx_copybutton',
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

# -- Options for HTML output

html_theme = 'sphinx_book_theme'
html_theme_options = {
    # 'logo_only': True,
    # 'show_toc_level': 2,
}

# -- Options for EPUB output
epub_show_urls = 'footnote'

# -- Options for sphinx-copybutton
copybutton_prompt_text = r'\$ '
copybutton_prompt_is_regexp = True

html_show_sourcelink = False
