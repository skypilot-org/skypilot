# Configuration file for the Sphinx documentation builder.

import os
import pathlib
import sys

import yaml

sys.path.insert(0, os.path.abspath('.'))
sys.path.insert(0, os.path.abspath('../'))
sys.path.insert(0, os.path.abspath('../../'))

import prepare_github_markdown

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
def render_svg_logo(path):
    with open(pathlib.Path(__file__).parent / path, 'r') as f:
        content = f.read()

    return content


# html_theme = 'sphinx_book_theme'
html_theme = 'pydata_sphinx_theme'
html_theme_options = {
    'show_toc_level': 1,
    'navbar_align': 'left',  # [left, content, right] For testing that the navbar items align properly
    'navbar_start': ['navbar-skypilot-logo'],
    'navbar_center': ['navbar-nav', 'navbar-icon-links'],
    'navbar_end': [
        # 'navbar-icon-links',
    ],
    'navbar_persistent': [
        'search-button-field',
        'theme-switcher',
    ],
    'logo': {
        'svg': render_svg_logo('_static/SkyPilot_wide_light.svg'),
    },
    "icon_links": [{
        "name": "Slack",
        "url": "https://slack.skypilot.co/",
        "icon": "fa-brands fa-slack",
    }, {
        "name": "Twitter",
        "url": "https://twitter.com/skypilot_org",
        "icon": "fa-brands fa-x-twitter",
    }, {
        "name": "GitHub",
        "url": "https://github.com/skypilot-org/skypilot/",
        "icon": "fa-brands fa-github",
    }],
    'use_edit_page_button': True,
    'announcement': None,
    'secondary_sidebar_items': [
        'page-toc',
        'edit-this-page',
    ],
    'navigation_depth': 4,
    'pygment_light_style': 'tango',
    'pygment_dark_style': 'monokai',
    'primary_sidebar_end': [],
    'footer_start': ['copyright'],
    'footer_center': [],
    'footer_end': [],
    'header_links_before_dropdown': 6,
}

html_context = {
    'github_user': 'skypilot-org',
    'github_repo': 'skypilot',
    'github_version': 'master',
    'doc_path': 'docs/source',
}

html_sidebars = {
    'index': [],
    '**': ['main-sidebar'],
}

# The name for this set of Sphinx documents.  If None, it defaults to
# '<project> v<release> documentation'.
html_title = 'SkyPilot documentation'

# -- Options for EPUB output
epub_show_urls = 'footnote'

# -- Options for sphinx-copybutton
copybutton_prompt_text = r'\$ '
copybutton_prompt_is_regexp = True

html_show_sourcelink = False

# The name of an image file (relative to this directory) to place at the top
# of the sidebar.
# html_logo = '_static/SkyPilot_wide_light.svg'

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
myst_heading_anchors = 7
show_sphinx = False


def add_metadata_to_page(app, pagename, templatename, context, doctree):
    # Add the author if it exists
    if app.config.author != 'unknown':
        context['author'] = app.config.author


def setup(app):
    # -- To demonstrate ReadTheDocs switcher -------------------------------------
    # This links a few JS and CSS files that mimic the environment that RTD uses
    # so that we can test RTD-like behavior. We don't need to run it on RTD and we
    # don't wanted it loaded in GitHub Actions because it messes up the lighthouse
    # results.
    if not os.environ.get('READTHEDOCS') and not os.environ.get(
            'GITHUB_ACTIONS'):
        app.add_css_file(
            'https://assets.readthedocs.org/static/css/readthedocs-doc-embed.css'
        )
        app.add_css_file(
            'https://assets.readthedocs.org/static/css/badge_only.css')

        # Create the dummy data file so we can link it
        # ref: https://github.com/readthedocs/readthedocs.org/blob/bc3e147770e5740314a8e8c33fec5d111c850498/readthedocs/core/static-src/core/js/doc-embed/footer.js  # noqa: E501
        app.add_js_file('rtd-data.js')
        app.add_js_file(
            'https://assets.readthedocs.org/static/javascript/readthedocs-doc-embed.js',
            priority=501,
        )

    app.connect('html-page-context', add_metadata_to_page)
    app.connect('builder-inited',
                prepare_github_markdown.handle_markdown_in_gallery)
