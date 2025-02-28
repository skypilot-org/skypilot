import re

from sphinx.application import Sphinx
from sphinx.util.logging import getLogger

# Add allowed technical terms and proper nouns here
ALLOWED_TERMS = {
    # Technical terms
    'Kubernetes',
    'SkyPilot',
    'SkyServe',
    'Gemma',
    'DeepSeek-R1',
    'Gradio',
    'OpenAI',
    'API',
    'GPU',
    'VM',
    'GUI',
    'AWS',
    'GCP',
    'Azure',
    'HF_TOKEN',
    'Ingress',
    'Helm',
    'Docker',
    'VSCode',
    'CLI',
    'SDK',
    'TPU',
    'Ray',
    'LoadBalancer',
    'Nginx',
    'Kubernetes',
    'Kubectl',
    'Weights & Biases',
    'Config',
    'Kueue',
    'Sky Computing',
    'Llama',
    'Llama2',
    # Product names with versioning
    'Llama 3.1',
    'Llama 3.2',
    'Vision Llama 3.2',
    'Mixtral 8x7b',
    # Cloud terms
    'Cloud',
    'Service Account',
    'Cloud Platform',
    'Cloud Infrastructure',
    # Common technical verbs
    'Deploy',
    'Run',
    'Serve',
    'Scale',
    'Connect',
    'Check',
    'Open',
    # Framework names
    'vLLM',
    'TGI',
    'RKE2',
    'DWS',
    'FAIR',
    'Qwen',
}


def check_sentence_case(app: Sphinx, docname: str, source: list):
    """Check heading style across Markdown and reST"""
    content = source[0]

    # Remove Markdown code blocks to avoid false positives
    content = re.sub(r'```.*?```', '', content, flags=re.DOTALL)

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
    # ([-`:"'~+^_#*]) - Capture underline character (group 2) from valid set
    # \2+          - 1+ repeats of same underline character
    # \s*$         - Optional trailing whitespace
    #
    # Note: Now includes '-' but excludes '=' (which is reserved for top-level)
    rst_pattern = re.compile(
        r'^([^\n]+)\n'  # Heading text
        r'([-`:"\'~+^_#*])'  # First underline char (now includes - but excludes =)
        r'\2+\s*$',  # Repeat same char + whitespace
        re.MULTILINE)
    headings += [m[0] for m in rst_pattern.findall(content)]

    for heading in headings:
        violations = []
        # Split on whitespace, preserving punctuation with words
        words = re.findall(r'\S+', heading)  # Changed splitting method
        step_pattern = re.compile(r'^step$', re.IGNORECASE)
        number_colon = re.compile(
            r'^\d+[:-]$')  # Matches digits followed by : or -
        identifier_pattern = re.compile(
            r'^[A-Za-z0-9]+$', re.IGNORECASE)  # Alphanumeric identifier
        punctuation_pattern = re.compile(
            r'^[:-]$')  # Punctuation as separate word

        for i in range(len(words)):
            word = words[i]

            # Skip allowed terms and acronyms
            if word in ALLOWED_TERMS or word.isupper():
                continue

            # Check for step pattern: "Step [identifier][punctuation?] [word]"
            if ((i >= 2 and step_pattern.match(words[i - 2]) and
                 (number_colon.match(words[i - 1]) or
                  identifier_pattern.match(words[i - 1]))) or
                (i >= 3 and step_pattern.match(words[i - 3]) and
                 identifier_pattern.match(words[i - 2]) and
                 punctuation_pattern.match(words[i - 1]))):
                continue

            # Allow version numbers and hyphens
            if re.search(r'([.-]\d|\d[.-])', word):
                continue

            # Check unexpected title case
            if i != 0 and word.istitle():
                violations.append(word)

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
