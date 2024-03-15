# Configuration file for the Sphinx documentation builder.

import os
import sys
import pathlib
import yaml


sys.path.insert(0, os.path.abspath('.'))
sys.path.insert(0, os.path.abspath('../'))
sys.path.insert(0, os.path.abspath('../../'))
from custom_directives import setup_context, update_context, parse_navbar_config

# -- Project information

project = 'SkyPilot'
copyright = '2024, SkyPilot Team'
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

autosummary_generate = True
napolean_use_rtype = False

# -- Options for autodoc

# Python methods should be presented in source code order
autodoc_member_order = 'bysource'

# -- Options for HTML output

# html_theme = 'sphinx_book_theme'
html_theme = 'pydata_sphinx_theme'
html_theme_options = {
    "show_toc_level": 1,
    "navbar_align": "left",  # [left, content, right] For testing that the navbar items align properly
    "navbar_center": ["navbar-nav"],
    # "navbar_start": ["navbar-skypilot-logo"],
    # "navbar_end": [
    #     "navbar-icon-links",
    # ],
    # "navbar_center": ["navbar-links"],
    "navbar_align": "left",
    "navbar_persistent": [
        "search-button-field",
    ],
    # "back_to_top_button": False,
    'logo': {
        'image_dark': '_static/SkyPilot_wide_dark.svg',
    },
    "use_edit_page_button": True,
    "announcement": None,
    "secondary_sidebar_items": [
        "page-toc",
        "edit-this-page",
    ],
    "navigation_depth": 4,
    'pygment_light_style': 'tango',
    'pygment_dark_style': 'monokai',
    'primary_sidebar_end': [],
}

html_context = {
    'github_user': 'skypilot-org',
    'github_repo': 'skypilot',
    'github_version': 'master',
    'doc_path': 'docs/source',
}

html_sidebars = {
    "**": ["main-sidebar"],
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
html_logo = '_static/SkyPilot_wide_light.svg'

# The name of an image file (within the static path) to use as favicon of the
# docs. This file should be a Windows icon file (.ico), 16x16 or 32x32 pixels.
html_favicon = '_static/favicon.ico'

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named 'default.css' will overwrite the builtin 'default.css'.
html_static_path = ['_static']
html_js_files = ['custom.js']
html_css_files = ['custom.css']

# Allowing cross references in markdown files to be parsed
myst_heading_anchors = 3


def setup(app):
    app.connect("html-page-context", update_context)

    app.add_config_value("navbar_content_path", "navbar.yml", "env")
    app.connect("config-inited", parse_navbar_config)
    app.connect("html-page-context", setup_context)
