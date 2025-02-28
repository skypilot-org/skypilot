import re

from sphinx.application import Sphinx
from sphinx.util.logging import getLogger

# Add allowed technical terms and proper nouns here
ALLOWED_TERMS = {
    # Technical terms
    'Kubernetes',
    'Llama-2',
    'Llama-3',
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
    # Product names with versioning
    'Llama 3.1',
    'Llama 3.2',
    'Vision Llama 3.2',
    'Mixtral 8x7b',
    # Cloud terms
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
    'FAIR'
}


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
        words = re.split(r'(\W+)', heading)  # Split with punctuation
        step_pattern = re.compile(r'^(step|step\s*\d+)[:-]?$', re.IGNORECASE)
        in_parentheses = False
        prev_word = ''

        for i in range(1, len(words)):
            word = words[i].strip()
            if not word:
                continue

            # Handle parentheses and special cases
            if word in ('(', '[', '{'):
                in_parentheses = True
                continue
            if word in (')', ']', '}'):
                in_parentheses = False
                continue
            if in_parentheses:
                continue

            # Skip allowed terms and acronyms
            if (word in ALLOWED_TERMS or any(term in ' '.join([prev_word, word])
                                             for term in ALLOWED_TERMS) or
                    word.isupper()):
                prev_word = word
                continue

            # Handle step patterns with different separators
            if step_pattern.match(words[i - 2]):
                if re.match(r'^(\d+|[-:])$', words[i - 1]):
                    prev_word = word
                    continue

            # Allow version patterns and hyphenated terms
            if re.search(r'(?i)(v?\d+([.-]\d+)*|[-]|^[\d.]+$)', word):
                prev_word = word
                continue

            # Allow code-like terms and CLI commands
            if (re.match(r'^`.*`$', word) or
                    re.match(r'^\$?[A-Za-z0-9_-]+\(\)$', word)):
                prev_word = word
                continue

            # Allow gerunds in specific technical contexts
            if (word.lower().endswith('ing') and
                    any(ctx in heading.lower()
                        for ctx in ['command', 'running', 'setting'])):
                prev_word = word
                continue

            # Check unexpected title case
            if word.istitle() and not word.isupper():
                violations.append(word)

            prev_word = word

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
