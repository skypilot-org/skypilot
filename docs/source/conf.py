# Configuration file for the Sphinx documentation builder.

import sys
import os
sys.path.insert(0, os.path.abspath('.'))
sys.path.insert(0, os.path.abspath('../'))
sys.path.insert(0, os.path.abspath('../prototype'))

# -- Project information

project = 'Sky'
copyright = '2022, Sky Team'
author = 'the Sky authors'

release = '0.1'
version = '0.1.0'

# -- General configuration

extensions = [
    'sphinx.ext.duration',
    'sphinx.ext.doctest',
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',
    'sphinx.ext.autosummary',
    'sphinx.ext.viewcode',
    'sphinx.ext.intersphinx',
    'sphinx_click',
    'sphinx_autodoc_typehints',
    'myst_nb',
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


# -- Options for myst
jupyter_execute_notebooks = "force"
execution_allow_errors = False

# Notebook cell execution timeout; defaults to 30.
execution_timeout = 100

always_document_param_types = True
html_show_sourcelink = True

napoleon_google_docstring = True
napoleon_use_param = False
napoleon_use_ivar = True
