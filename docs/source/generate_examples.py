# Adapted from vllm:
# https://github.com/vllm-project/vllm/blob/01c184b8f3d98d842ffe0022583decb70bf75582/docs/source/generate_examples.py
# SPDX-License-Identifier: Apache-2.0

from dataclasses import dataclass, field
import itertools
from pathlib import Path
import re
import subprocess

ROOT_DIR = Path(__file__).parent.parent.parent.resolve()
ROOT_DIR_RELATIVE = '../../../..'

# Scan this subdir and its subdirs.
EXAMPLE_DIRS = [
    ROOT_DIR / 'llm',
    ROOT_DIR / 'examples',
]

# File suffixes to include as the examples.
_GLOB_PATTERNS = [
    '*.md',
    '*.yaml',
]

# Output generated markdown files to this dir.
EXAMPLE_DOC_DIR = ROOT_DIR / 'docs/source/generated-examples'

_SUFFIX_TO_LANGUAGE = {
    '.md': 'markdown',
    '.yaml': 'yaml',
    '.py': 'python',
    '.sh': 'bash',
}


def fix_case(text: str) -> str:
    subs = {
        'api': 'API',
        'Cli': 'CLI',
        'cpu': 'CPU',
        'llm': 'LLM',
        'tpu': 'TPU',
        'aqlm': 'AQLM',
        'gguf': 'GGUF',
        'lora': 'LoRA',
        'vllm': 'vLLM',
        'openai': 'OpenAI',
        'multilora': 'MultiLoRA',
        'mlpspeculator': 'MLPSpeculator',
        r'fp\d+': lambda x: x.group(0).upper(),  # e.g. fp16, fp32
        r'int\d+': lambda x: x.group(0).upper(),  # e.g. int8, int16
    }
    for pattern, repl in subs.items():
        text = re.sub(rf'\b{pattern}\b', repl, text, flags=re.IGNORECASE)
    return text


@dataclass
class Example:
    """
    Example class for generating documentation content from a given path.

    Attributes:
        path (Path): The path to the main directory or file.
        category (str): The category of the document.
        main_file (Path): The main file in the directory.
        other_files (list[Path]): List of other files in the directory.
        title (str): The title of the document.

    Methods:
        __post_init__(): Initializes the main_file, other_files, and title attributes.
        determine_main_file() -> Path: Determines the main file in the given path.
        determine_other_files() -> list[Path]: Determines other files in the directory excluding the main file.
        determine_title() -> str: Determines the title of the document.
        generate() -> str: Generates the documentation content.
    """ # noqa: E501
    path: Path
    category: str = None
    main_file: Path = field(init=False)
    other_files: list[Path] = field(init=False)
    title: str = field(init=False)

    def __post_init__(self):
        self.main_file = self.determine_main_file()
        self.other_files = self.determine_other_files()
        self.title = self.determine_title()

    def determine_main_file(self) -> Path:
        """
        Determines the main file in the given path.
        If the path is a file, it returns the path itself. Otherwise, it searches
        for Markdown files (*.md) in the directory and returns the first one found.
        Returns:
            Path: The main file path, either the original path if it's a file or the first
            Markdown file found in the directory.
        Raises:
            IndexError: If no Markdown files are found in the directory.
        """ # noqa: E501
        return self.path if self.path.is_file() else list(
            self.path.glob('*.md')).pop()

    def determine_other_files(self) -> list[Path]:
        """
        Determine other files in the directory excluding the main file.

        This method checks if the given path is a file. If it is, it returns an empty list.
        Otherwise, it recursively searches through the directory and returns a list of all
        files that are not the main file and are tracked by git.

        Returns:
            list[Path]: A list of Path objects representing the other git-tracked files in the directory.
        """ # noqa: E501
        if self.path.is_file():
            return []

        # Get all files in the directory
        all_files = [
            file for file in self.path.rglob('*')
            if file.is_file() and file != self.main_file
        ]

        # Filter for git-tracked files only
        git_tracked_files = []
        for file in all_files:
            try:
                # Use git ls-files to check if the file is tracked
                result = subprocess.run([
                    'git', 'ls-files', '--error-unmatch',
                    str(file.relative_to(ROOT_DIR))
                ],
                                        cwd=ROOT_DIR,
                                        stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE,
                                        check=False)
                if result.returncode == 0:  # Return code 0 means file is tracked
                    git_tracked_files.append(file)
            except Exception:
                # Skip files that cause errors
                continue

        return git_tracked_files

    def determine_title(self) -> str:
        return fix_case(self.path.stem.replace('_', ' ').title())

    def generate(self) -> str:
        # Convert the path to a relative path from __file__
        make_relative = lambda path: ROOT_DIR_RELATIVE / path.relative_to(
            ROOT_DIR)

        content = f'Source: <gh-file:{self.path.relative_to(ROOT_DIR)}>\n\n'
        include = 'include' if self.main_file.suffix == '.md' else \
            'literalinclude'
        if include == 'literalinclude':
            content += f'# {self.title}\n\n'
        content += f':::{{{include}}} {make_relative(self.main_file)}\n'
        if include == 'literalinclude':
            content += f':language: {self.main_file.suffix[1:]}\n'
        content += ':::\n\n'

        if not self.other_files:
            return content

        content += '## Included files\n\n'
        for file in sorted(self.other_files):
            include = 'include' if file.suffix == '.md' else 'literalinclude'
            content += f':::{{admonition}} {file.relative_to(self.path)}\n'
            content += ':class: dropdown\n\n'
            content += f':::{{{include}}} {make_relative(file)}\n'
            if include == 'literalinclude' and file.suffix in _SUFFIX_TO_LANGUAGE:
                content += f':language: {_SUFFIX_TO_LANGUAGE[file.suffix]}\n'
            content += ':::\n:::\n\n'

        return content


def generate_examples():
    # Create the EXAMPLE_DOC_DIR if it doesn't exist
    if not EXAMPLE_DOC_DIR.exists():
        EXAMPLE_DOC_DIR.mkdir(parents=True)

    for example_dir in EXAMPLE_DIRS:
        _work(example_dir)


def _work(example_dir: Path):
    examples = []

    # Find uncategorised examples
    globs = [example_dir.glob(pattern) for pattern in _GLOB_PATTERNS]
    for path in itertools.chain(*globs):
        examples.append(Example(path))
    # Find examples in subdirectories
    for path in example_dir.glob("*/*.md"):
        examples.append(Example(path.parent))

    # Check for stem collisions using full directory names
    stems = {}
    for example in examples:
        stem = example.path.name  # Use full directory name instead of stem
        if stem in stems and stems[stem] != example.path:
            raise ValueError(
                f'Collision detected: Multiple examples with stem "{stem}".\n'
                f'First path: {stems[stem]}\n'
                f'Second path: {example.path}\n'
                'Please rename one of them.'
            )
        stems[stem] = example.path
        example.title = fix_case(stem.replace('_', ' ').title())  # Update title accordingly

    # Generate the example documentation using the updated stem
    for example in sorted(examples, key=lambda e: e.path.name):
        doc_path = EXAMPLE_DOC_DIR / f'{example.path.name}.md'
        with open(doc_path, 'w+') as f:
            f.write(example.generate())


if __name__ == '__main__':
    generate_examples()
