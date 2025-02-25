import re

from sphinx.application import Sphinx
from sphinx.util.logging import getLogger


def check_sentence_case(app: Sphinx, docname: str, source: list):
    """Check heading style across Markdown and reST"""
    content = source[0]
    headings = []

    # Markdown ATX headings pattern explanation:
    # ^            - Start of line
    # #{2,}        - 2+ '#' characters (heading level, excluding top level)
    # \s+          - 1+ whitespace after #
    # (.+?)        - Non-greedy capture of heading text (group 1)
    # \s*          - Optional whitespace after heading text
    # #*           - Optional closing #s (some markdown flavors allow this)
    # $            - End of line
    md_pattern = re.compile(r'^#{2,}\s+(.+?)\s*#*$', re.MULTILINE)
    headings += md_pattern.findall(content)

    # reST underlined headings pattern explanation:
    # We need to identify the document structure to exclude top-level headings
    # This simplified approach looks for specific underline characters
    # typically used for section levels below the top level
    #
    # ^            - Start of line
    # ([^\n]+)     - Capture heading text (group 1) - any chars except newline
    # \n           - Newline after heading text
    # ([=\-`:"'~+^_#*]) - Capture underline character (group 2) from valid set
    # \2+          - 1+ repeats of same underline character
    # \s*$         - Optional trailing whitespace
    #
    # Note: Typically = and - are used for top-level headings, so we exclude them
    rst_pattern = re.compile(
        r'^([^\n]+)\n'  # Heading text
        r'([`:"\'~+^_#*])'  # First underline char (excluding = and - for top level)
        r'\2+\s*$',  # Repeat same char + whitespace
        re.MULTILINE)
    headings += [m[0] for m in rst_pattern.findall(content)]

    for heading in headings:
        words = heading.split()
        violations = [
            word for word in words[1:]  # Skip first word
            if word.istitle() and not word.isupper()  # Allow acronyms like NASA
        ]

        if violations:
            logger = getLogger(__name__)
            logger.warning(
                f"Heading case issue: '{heading}' - "
                f"Unexpected capitals: {', '.join(violations)}",
                location=docname,
                type='linting',
                subtype='heading-style')


def setup(app: Sphinx):
    """Extension setup"""
    app.connect('source-read', check_sentence_case)
    return {'version': '0.1'}
