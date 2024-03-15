from collections.abc import Iterable
from enum import Enum
import re
import sphinx
from typing import List, Dict, Union, Callable, Any, Optional, Tuple
import copy
import yaml
import bs4
import logging
import logging.handlers
import pathlib
import random
import urllib
import urllib.request
from queue import Queue
import requests
from sphinx.util import logging as sphinx_logging
from sphinx.util.console import red  # type: ignore
from sphinx.util.nodes import make_refnode
from docutils import nodes
from functools import lru_cache
from pydata_sphinx_theme.toctree import add_collapse_checkboxes

from pygments import highlight
from pygments.lexers import PythonLexer
from pygments.formatters import HtmlFormatter


logger = logging.getLogger(__name__)

__all__ = [
    "DownloadAndPreprocessEcosystemDocs",
    "update_context",
    "LinkcheckSummarizer",
    "setup_context",
]

# Taken from https://github.com/edx/edx-documentation
FEEDBACK_FORM_FMT = (
    "https://github.com/ray-project/ray/issues/new?"
    "title={title}&labels=docs&body={body}"
)

EXAMPLE_GALLERY_CONFIGS = [
    "source/data/examples.yml",
    "source/serve/examples.yml",
    "source/train/examples.yml",
]


def feedback_form_url(project, page):
    """Create a URL for feedback on a particular page in a project."""
    return FEEDBACK_FORM_FMT.format(
        title=urllib.parse.quote("[docs] Issue on `{page}.rst`".format(page=page)),
        body=urllib.parse.quote(
            "# Documentation Problem/Question/Comment\n"
            "<!-- Describe your issue/question/comment below. -->\n"
            "<!-- If there are typos or errors in the docs, feel free "
            "to create a pull-request. -->\n"
            "\n\n\n\n"
            "(Created directly from the docs)\n"
        ),
    )


def update_context(app, pagename, templatename, context, doctree):
    """Update the page rendering context to include ``feedback_form_url``."""
    context["feedback_form_url"] = feedback_form_url(app.config.project, pagename)


# Add doc files from external repositories to be downloaded during build here
# (repo, ref, path to get, path to save on disk)
EXTERNAL_MARKDOWN_FILES = []


class _BrokenLinksQueue(Queue):
    """Queue that discards messages about non-broken links."""

    def __init__(self, maxsize: int = 0) -> None:
        self._last_line_no = None
        self.used = False
        super().__init__(maxsize)

    def put(self, item: logging.LogRecord, block=True, timeout=None):
        self.used = True
        message = item.getMessage()
        # line nos are separate records
        if ": line" in message:
            self._last_line_no = item
        # same formatting as in sphinx.builders.linkcheck
        # to avoid false positives if "broken" is in url
        if red("broken    ") in message or "broken link:" in message:
            if self._last_line_no:
                super().put(self._last_line_no, block=block, timeout=timeout)
                self._last_line_no = None
            return super().put(item, block=block, timeout=timeout)


class _QueueHandler(logging.handlers.QueueHandler):
    """QueueHandler without modifying the record."""

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        return record


class LinkcheckSummarizer:
    """Hook into the logger used by linkcheck to display a summary at the end."""

    def __init__(self) -> None:
        self.logger = None
        self.queue_handler = None
        self.log_queue = _BrokenLinksQueue()

    def add_handler_to_linkcheck(self, *args, **kwargs):
        """Adds a handler to the linkcheck logger."""
        self.logger = sphinx_logging.getLogger("sphinx.builders.linkcheck")
        self.queue_handler = _QueueHandler(self.log_queue)
        if not self.logger.hasHandlers():
            # If there are no handlers, add the one that would
            # be used anyway.
            self.logger.logger.addHandler(logging.lastResort)
        self.logger.logger.addHandler(self.queue_handler)

    def summarize(self, *args, **kwargs):
        """Summarizes broken links."""
        if not self.log_queue.used:
            return

        self.logger.logger.removeHandler(self.queue_handler)

        self.logger.info("\nBROKEN LINKS SUMMARY:\n")
        has_broken_links = False
        while self.log_queue.qsize() > 0:
            has_broken_links = True
            record: logging.LogRecord = self.log_queue.get()
            self.logger.handle(record)

        if not has_broken_links:
            self.logger.info("No broken links found!")


def parse_navbar_config(app: sphinx.application.Sphinx, config: sphinx.config.Config):
    """Parse the navbar config file into a set of links to show in the navbar.

    Parameters
    ----------
    app : sphinx.application.Sphinx
        Application instance passed when the `config-inited` event is emitted
    config : sphinx.config.Config
        Initialized configuration to be modified
    """
    if "navbar_content_path" in config:
        filename = app.config["navbar_content_path"]
    else:
        filename = ""

    if filename:
        with open(pathlib.Path(__file__).parent / filename, "r") as f:
            config.navbar_content = yaml.safe_load(f)
    else:
        config.navbar_content = None


NavEntry = Dict[str, Union[str, List["NavEntry"]]]


def preload_sidebar_nav(
    get_toctree: Callable[[Any], str],
    pathto: Callable[[str], str],
    root_doc: str,
    pagename: str,
) -> bs4.BeautifulSoup:
    """Return the navigation link structure in HTML.

    This function is modified from the pydata_sphinx_theme function
    `generate_toctree_html`. However, for ray we only produce one
    sidebar for all pages. We therefore can call this function just once (per worker),
    cache the result, and reuse it.

    The result of this function is equivalent to calling

        pydata_sphinx_theme.toctree.generate_toctree_html(
            "sidebar",
            startdepth=0,
            show_nav_level=0,
            collapse=False,
            includehidden=True,
            maxdepth=4,
            titles_only=True
        )

    Here we cache the result on this function itself because the `get_toctree`
    function is not the same instance for each `html-page-context` event, even
    if the toctree content is identical.

    Parameters
    ----------
    get_toctree : Callable[[Any], str]
        The function defined in context["toctree"] when html-page-context is triggered

    Returns
    -------
    bs4.BeautifulSoup
        Soup to display in the side navbar
    """
    if hasattr(preload_sidebar_nav, "cached_toctree"):
        # Need to retrieve a copy of the cached toctree HTML so as not to modify
        # the cached version when we set the "checked" state of the inputs
        soup = copy.copy(preload_sidebar_nav.cached_toctree)
    else:
        toctree = get_toctree(collapse=False, includehidden=True, maxdepth=4, titles_only=True)
        with open('toctree.txt', 'a') as f:
            print(toctree, file=f)
        soup = bs4.BeautifulSoup(toctree, "html.parser")

        for li in soup("li", {"class": "current"}):
            li["class"].remove("current")

        for li in soup.select("li"):
            if li.find("a"):
                href = li.find("a")["href"]
                if "#" in href and href != "#":
                    li.decompose()

        for ul in soup("ul", recursive=False):
            ul.attrs["class"] = ul.attrs.get("class", []) + ["nav", "bd-sidenav"]

        # Adjusted section for handling part captions and creating dropdowns
        with open('html.txt', 'a') as f:
            print(soup.string, file=f)
        captions = soup.find_all("p", attrs={"class": "caption"})
        with open('captions.txt', 'a') as f:
            print(captions, file=f)
        for caption in captions:
            ul = caption.find_next_sibling("ul")
            if ul:
                wrapper_div = soup.new_tag("div", attrs={"class": "dropdown-container"})
                dropdown_input = soup.new_tag("input", attrs={"id": caption.text.strip(), "type": "checkbox", "class": "dropdown-toggle"})
                dropdown_label = soup.new_tag("label", attrs={"for": caption.text.strip(), "class": "dropdown-label"})
                dropdown_label.string = caption.text
                caption.replace_with(wrapper_div)
                wrapper_div.extend([dropdown_input, dropdown_label, ul])

        add_collapse_checkboxes(soup)

        preload_sidebar_nav.cached_toctree = copy.copy(soup)

    if pagename == root_doc:
        to_root_prefix = "./"
    else:
        to_root_prefix = re.sub(f"{root_doc}.html", "", pathto(root_doc))

    for a in soup.select("a"):
        absolute_href = re.sub(r"^(\.\.\/)*", "", a["href"])
        a["href"] = to_root_prefix + absolute_href

        if absolute_href == f"{pagename}.html":
            parent_li = a.find_parent("li")
            parent_li["class"] = parent_li.get("class", []) + ["current-page"]

            for parent_li in a.find_parents("li", attrs={"class": "has-children"}):
                el = parent_li.find("input")
                if el:
                    el.attrs["checked"] = True

    return soup



class ExampleEnum(Enum):
    """Enum which allows easier enumeration of members for example metadata."""

    @classmethod
    def items(cls: type) -> Iterable[Tuple["ExampleEnum", str]]:
        """Return an iterable mapping between the enum type and the corresponding value.

        Returns
        -------
        Dict['ExampleEnum', str]
            Dictionary of enum type, enum value for the enum class
        """
        yield from {entry: entry.value for entry in cls}.items()

    @classmethod
    def values(cls: type) -> List[str]:
        """Return a list of the values of the enum.

        Returns
        -------
        List[str]
            List of values for the enum class
        """
        return [entry.value for entry in cls]

    @classmethod
    def _missing_(cls: type, value: str) -> "ExampleEnum":
        """Allow case-insensitive lookup of enum values.

        This shouldn't be called directly. Instead this is called when e.g.,
        "SkillLevel('beginner')" is called.

        Parameters
        ----------
        value : str
            Value for which the associated enum class instance is to be returned.
            Spaces are stripped from the beginning and end of the string, and
            matching is case-insensitive.

        Returns
        -------
        ExampleEnum
            Enum class instance for the requested value
        """
        value = value.lstrip().rstrip().lower()
        for member in cls:
            if member.value.lstrip().rstrip().lower() == value:
                return member
        return None

    @classmethod
    def formatted_name(cls: type) -> str:
        """Return the formatted name for the class."""
        raise NotImplementedError





def setup_context(app, pagename, templatename, context, doctree):

    @lru_cache(maxsize=None)
    def render_header_nav_links() -> bs4.BeautifulSoup:
        """Render external header links into the top nav bar.
        The structure rendered here is defined in an external yaml file.

        Returns
        -------
        str
            Raw HTML to be rendered in the top nav bar
        """
        if not hasattr(app.config, "navbar_content"):
            raise ValueError(
                "A template is attempting to call render_header_nav_links(); a "
                "navbar configuration must be specified."
            )

        node = nodes.container(classes=["navbar-content"])
        node.append(render_header_nodes(app.config.navbar_content))
        header_soup = bs4.BeautifulSoup(
            app.builder.render_partial(node)["fragment"], "html.parser"
        )
        return add_nav_chevrons(header_soup)

    def render_header_nodes(
        obj: List[NavEntry], is_top_level: bool = True
    ) -> nodes.Node:
        """Generate a set of header nav links with docutils nodes.

        Parameters
        ----------
        is_top_level : bool
            True if the call to this function is rendering the top level nodes,
            False otherwise (non-top level nodes are displayed as submenus of the top
            level nodes)
        obj : List[NavEntry]
            List of yaml config entries to render as docutils nodes

        Returns
        -------
        nodes.Node
            Bullet list which will be turned into header nav HTML by the sphinx builder
        """
        bullet_list = nodes.bullet_list(
            bullet="-",
            classes=["navbar-toplevel" if is_top_level else "navbar-sublevel"],
        )

        for item in obj:

            if "file" in item:
                ref_node = make_refnode(
                    app.builder,
                    context["current_page_name"],
                    item["file"],
                    None,
                    nodes.inline(classes=["navbar-link-title"], text=item.get("title")),
                    item.get("title"),
                )
            elif "link" in item:
                ref_node = nodes.reference("", "", internal=False)
                ref_node["refuri"] = item.get("link")
                ref_node["reftitle"] = item.get("title")
                ref_node.append(
                    nodes.inline(classes=["navbar-link-title"], text=item.get("title"))
                )

            if "caption" in item:
                caption = nodes.Text(item.get("caption"))
                ref_node.append(caption)

            paragraph = nodes.paragraph()
            paragraph.append(ref_node)

            container = nodes.container(classes=["ref-container"])
            container.append(paragraph)

            list_item = nodes.list_item(
                classes=["active-link"] if item.get("file") == pagename else []
            )
            list_item.append(container)

            if "sections" in item:
                wrapper = nodes.container(classes=["navbar-dropdown"])
                wrapper.append(
                    render_header_nodes(item["sections"], is_top_level=False)
                )
                list_item.append(wrapper)

            bullet_list.append(list_item)

        return bullet_list

    context["cached_toctree"] = preload_sidebar_nav(
        context["toctree"],
        context["pathto"],
        context["root_doc"],
        pagename,
        doctree,
    )
    context["render_header_nav_links"] = render_header_nav_links

    # Update the HTML page context with a few extra utilities.
    context["pygments_highlight_python"] = lambda code: highlight(
        code, PythonLexer(), HtmlFormatter()
    )


def update_hrefs(input_soup: bs4.BeautifulSoup, n_levels_deep=0):
    soup = copy.copy(input_soup)
    for a in soup.select("a"):
        a["href"] = "../" * n_levels_deep + a["href"]

    return soup


def add_nav_chevrons(input_soup: bs4.BeautifulSoup) -> bs4.BeautifulSoup:
    """Add dropdown chevron icons to the header nav bar.

    Parameters
    ----------
    input_soup : bs4.BeautifulSoup
        Soup containing rendered HTML which will be inserted into the header nav bar

    Returns
    -------
    bs4.BeautifulSoup
        A new BeautifulSoup instance containing chevrons on the list items that
        are meant to be dropdowns.
    """
    soup = copy.copy(input_soup)

    for li in soup.find_all("li", recursive=True):
        divs = li.find_all("div", {"class": "navbar-dropdown"}, recursive=False)
        if divs:
            ref = li.find("div", {"class": "ref-container"})
            ref.append(soup.new_tag("i", attrs={"class": "fa-solid fa-chevron-down"}))

    return soup


def render_example_gallery_dropdown(cls: type) -> bs4.BeautifulSoup:
    """Render a dropdown menu selector for the example gallery.

    Parameters
    ----------
    cls : type
        ExampleEnum class type to use to populate the dropdown

    Returns
    -------
    bs4.BeautifulSoup
        Soup containing the dropdown element
    """
    soup = bs4.BeautifulSoup()

    dropdown_name = cls.formatted_name().lower().replace(" ", "-")
    dropdown_container = soup.new_tag(
        "div", attrs={"class": "filter-dropdown", "id": f"{dropdown_name}-dropdown"}
    )

    dropdown_show_checkbox = soup.new_tag(
        "input",
        attrs={
            "class": "dropdown-checkbox",
            "id": f"{dropdown_name}-checkbox",
            "type": "checkbox",
        },
    )
    dropdown_container.append(dropdown_show_checkbox)

    dropdown_label = soup.new_tag(
        "label", attrs={"class": "dropdown-label", "for": f"{dropdown_name}-checkbox"}
    )
    dropdown_label.append(cls.formatted_name())
    chevron = soup.new_tag("i", attrs={"class": "fa-solid fa-chevron-down"})
    dropdown_label.append(chevron)
    dropdown_container.append(dropdown_label)

    if cls.values():
        dropdown_options = soup.new_tag("div", attrs={"class": "dropdown-content"})

        for member in list(cls):
            label = soup.new_tag("label", attrs={"class": "checkbox-container"})
            label.append(member.value)

            tag = getattr(member, "tag", member.value)
            checkbox = soup.new_tag(
                "input",
                attrs={
                    "id": f"{tag}-checkbox",
                    "class": "filter-checkbox",
                    "type": "checkbox",
                },
            )
            label.append(checkbox)

            checkmark = soup.new_tag("span", attrs={"class": "checkmark"})
            label.append(checkmark)

            dropdown_options.append(label)

        dropdown_container.append(dropdown_options)

    soup.append(dropdown_container)
    return soup


def pregenerate_example_rsts(
    app: sphinx.application.Sphinx, *example_configs: Optional[List[str]]
):
    """Pregenerate RST files for the example page configuration files.

    This generates RST files for displaying example pages for Ray libraries.
    See `add_custom_assets` for more information about the custom template
    that gets rendered from these configuration files.

    Parameters
    ----------
    *example_configs : Optional[List[str]]
        Configuration files for which example pages are to be generated
    """
    if not example_configs:
        example_configs = EXAMPLE_GALLERY_CONFIGS

    for config in example_configs:
        # Depending on where the sphinx build command is run from, the path to the
        # target config file can change. Handle these paths manually to ensure it
        # works on RTD and locally.
        config_path = pathlib.Path(app.confdir) / pathlib.Path(config).relative_to(
            "source"
        )
        page_title = "Examples"
        title_decoration = "=" * len(page_title)
        with open(config_path.with_suffix(".rst"), "w") as f:
            f.write(
                f"{page_title}\n{title_decoration}\n\n"
                "  .. this file is pregenerated; please edit ./examples.yml to "
                "modify examples for this library."
            )

