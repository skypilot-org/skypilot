# Configuration file for the Sphinx documentation builder.

import os
import pathlib
import sys

sys.path.insert(0, os.path.abspath('.'))
sys.path.insert(0, os.path.abspath('../'))
sys.path.insert(0, os.path.abspath('../../'))

import generate_examples

# -- Project information

project = 'SkyPilot'
copyright = '2025, SkyPilot Team'
author = 'the SkyPilot authors'

# The version info for the project you're documenting, acts as replacement for
# |version| and |release|, also used in various other places throughout the
# built documents.
from sky import __version__ as version

# The full version, including alpha/beta/rc tags.
release = version

# -- General configuration

extensions = [
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
    "sphinx_togglebutton",  # Used for collapsible sections in examples.
    'sphinxcontrib.googleanalytics',
    'sphinxemoji.sphinxemoji',
    'sphinx_design',
    'myst_parser',
    'notfound.extension',
    'sphinx.ext.autosectionlabel',
    'extension.linting',
    'extension.markdown_export',
    'extension.dynamic_llms_txt',
]
# Needed for admonitions in markdown:
# https://myst-parser.readthedocs.io/en/latest/syntax/admonitions.html
myst_enable_extensions = [
    "colon_fence",
    # Converts http links to clickable links in HTML output.
    "linkify",
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
# Disable automatic type hints to let Napoleon handle them
napoleon_use_rtype = False
# Napoleon settings
napoleon_custom_sections = [
    ('Request Returns', 'params_style'),
    ('Request Raises', 'params_style'),
]

# -- Options for autodoc

# Python methods should be presented in source code order
autodoc_member_order = 'bysource'

# Mock imports for modules that should not be loaded during doc build
autodoc_mock_imports = [
    'sky.schemas.generated',
]


# -- Options for HTML output
def render_svg_logo(path):
    with open(pathlib.Path(__file__).parent / path, 'r') as f:
        content = f.read()

    return content


# Add extra paths that contain custom files here, relative to this directory.
# These files are copied directly to the root of the documentation.
html_extra_path = ['robots.txt']

# html_theme = 'sphinx_book_theme'
html_theme = 'pydata_sphinx_theme'
html_theme_options = {
    'show_toc_level': 1,
    'navbar_align': 'left',  # [left, content, right] For testing that the navbar items align properly
    'navbar_start': ['navbar-skypilot-logo'],
    'navbar_center': ['navbar-nav'],
    'navbar_end': [
        'theme-switcher',
        'navbar-icon-links',
    ],
    'navbar_persistent': ['search-button-field'],
    'logo': {
        'svg': render_svg_logo('_static/SkyPilot_wide_light.svg'),
    },
    'icon_links': [{
        'name': 'Slack',
        'url': 'https://slack.skypilot.co/',
        'icon': 'fab fa-slack',
    }, {
        'name': 'Twitter',
        'url': 'https://twitter.com/skypilot_org',
        'icon': 'fab fa-twitter',
    }, {
        'name': 'GitHub',
        'url': 'https://github.com/skypilot-org/skypilot/',
        'icon': 'fab fa-github',
    }],
    'use_edit_page_button': True,
    'announcement': '',  # Put announcements such as meetups here.
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
    'article_header_start': None,  # Disable breadcrumbs.
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

exclude_patterns = [
    '_gallery_original',
    'generated-examples',
]
myst_url_schemes = {
    'http': None,
    'https': None,
    'mailto': None,
    'ftp': None,
    'local': {
        'url': '{{path}}',
        'title': '{{path}}',
    },
    'gh-issue': {
        'url': 'https://github.com/skypilot-org/skypilot/issues/{{path}}#{{fragment}}',
        'title': 'Issue #{{path}}',
        'classes': ['github'],
    },
    'gh-pr': {
        'url': 'https://github.com/skypilot-org/skypilot/pull/{{path}}#{{fragment}}',
        'title': 'Pull Request #{{path}}',
        'classes': ['github'],
    },
    'gh-dir': {
        'url': 'https://github.com/skypilot-org/skypilot/tree/master/{{path}}',
        'title': '{{path}}',
        'classes': ['github'],
    },
    'gh-file': {
        'url': 'https://github.com/skypilot-org/skypilot/blob/master/{{path}}',
        'title': '{{path}}',
        'classes': ['github'],
    },
}

googleanalytics_id = 'G-92WF3MDCJV'

autosectionlabel_prefix_document = True

suppress_warnings = ['autosectionlabel.*']

# Adapted from vllm-project/vllm
# see https://docs.readthedocs.io/en/stable/reference/environment-variables.html # noqa
READTHEDOCS_VERSION_TYPE = os.environ.get('READTHEDOCS_VERSION_TYPE')
if READTHEDOCS_VERSION_TYPE == "tag":
    # remove the warning banner if the version is a tagged release
    header_file = os.path.join(os.path.dirname(__file__),
                               "_templates/header.html")
    # The file might be removed already if the build is triggered multiple times
    # (readthedocs build both HTML and PDF versions separately)
    if os.path.exists(header_file):
        os.remove(header_file)


def copy_example_assets(app, exception):
    """Copy example assets to the build directory after HTML build."""
    if exception is not None:
        return

    import shutil
    import glob

    # Only copy assets for HTML builds
    if app.builder.name != 'html':
        return

    # Find the examples directory relative to docs/source
    examples_dir = os.path.abspath(os.path.join(app.srcdir, '../../examples'))

    if os.path.exists(examples_dir):
        # Find all assets directories in examples
        for assets_dir in glob.glob(os.path.join(examples_dir, '*/assets')):
            if os.path.exists(assets_dir):
                # Destination directory in the built HTML
                dest_dir = os.path.join(app.outdir, 'examples', 'applications',
                                        'assets')
                os.makedirs(dest_dir, exist_ok=True)

                # Copy all files from assets directory
                for file in os.listdir(assets_dir):
                    src = os.path.join(assets_dir, file)
                    dst = os.path.join(dest_dir, file)
                    if os.path.isfile(src):
                        shutil.copy2(src, dst)
                        print(f'Copied asset: {file}')


def setup(app):
    # Run generate_examples directly during setup instead of connecting to builder-inited
    # This ensures it completes fully before any build steps start
    generate_examples.generate_examples(app)

    # Connect the asset copying to the build-finished event
    app.connect('build-finished', copy_example_assets)
