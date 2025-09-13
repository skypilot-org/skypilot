import re

from sphinx.application import Sphinx
from sphinx.util.logging import getLogger

# Add allowed technical terms and proper nouns here
ALLOWED_TERMS = {
    # Technical terms
    'Kubernetes',
    'Python',
    'SkyPilot',
    'SkyServe',
    'Gemma',
    'DeepSeek-R1',
    'Gradio',
    'OpenAI',
    'API',
    'GPU',
    'GPT-OSS',
    'VM',
    'GUI',
    'AWS',
    'GCP',
    'Nebius',
    'Bonus',
    'Infiniband',
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
    'Kueue',
    'Sky',
    'Llama',
    'Llama2',
    'Pods',
    'Samsung',
    'Google',
    'Amazon',
    'Okta',
    'Prometheus',
    'Grafana',
    # Framework names
    'vLLM',
    'TGI',
    'RKE2',
    'DWS',
    'FAIR',
    'Qwen',
    # Area
    'Europe',
}

# Add multi-word terms that should be treated as a single entity
MULTI_WORD_TERMS = {
    'Lambda Cloud',
    'Weights & Biases',
    'Rancher Kubernetes Engine',
    'Google Cloud',
    'LoadBalancer Service',
    'Dynamic Workload Scheduler',
    'Sky Computing',
    'VS Code',
    'Cudo Compute',
    'Samsung Cloud Platform',
    'Node Pool',
    'Node Pools',
    'OAuth2 Proxy',
    'Google Workspace',
    'Google Auth Platform',
    'Google Cloud Logging',
    'AWS Systems Manager',
    'Microsoft Entra ID',
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
        words = re.findall(r'\S+', heading)

        i = 0
        while i < len(words):
            # Check for multi-word terms
            matched_phrase = False
            # Sort by length descending to match longer phrases first
            for phrase in sorted(MULTI_WORD_TERMS, key=len, reverse=True):
                phrase_words = phrase.split()
                if i + len(phrase_words) <= len(words):
                    # Join words first, then strip punctuation at the end for comparison
                    joined_words = ' '.join(words[i:i + len(phrase_words)])
                    # Strip punctuation at both the beginning and end, preserving middle punctuation
                    stripped_joined = re.sub(r'^\W+|\W+$', '', joined_words)
                    if stripped_joined == phrase:
                        # Skip all words in the matched phrase
                        i += len(phrase_words)
                        matched_phrase = True
                        break

            if matched_phrase:
                continue

            original_word = words[i]
            # Remove ALL leading/trailing non-alphanumeric characters
            stripped_word = re.sub(r'^\W+|\W+$', '', original_word)

            # Skip allowed terms and acronyms (check stripped version)
            if stripped_word in ALLOWED_TERMS or stripped_word.isupper():
                i += 1
                continue

            # Skip if previous word ends with punctuation (like "Step 1:", "1.", "Part:")
            if i >= 1 and re.search(r'[:.)\]-]$', words[i - 1]):
                i += 1
                continue

            # Allow version numbers and hyphens
            if re.search(r'([.-]\d|\d[.-])', original_word):
                i += 1
                continue

            # Check unexpected title case (skip if previous word ends with : or ) or ])
            if (i != 0 and original_word.istitle() and
                    not words[i - 1].endswith((':', ')', ']'))):
                violations.append(original_word)

            i += 1

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
