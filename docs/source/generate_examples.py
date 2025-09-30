# Adapted from vllm:
# https://github.com/vllm-project/vllm/blob/01c184b8f3d98d842ffe0022583decb70bf75582/docs/source/generate_examples.py
# SPDX-License-Identifier: Apache-2.0

import dataclasses
import itertools
import pathlib
import re
import subprocess
from typing import Optional

ROOT_DIR = pathlib.Path(__file__).parent.parent.parent.resolve()
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


def preprocess_github_markdown_file(source_path: str,
                                    dest_path: Optional[str] = None):
    """
    Preprocesses GitHub Markdown files by:
        - Uncommenting all ``<!-- -->`` comments in which opening tag is immediately
          succeeded by ``$UNCOMMENT``(eg. ``<!--$UNCOMMENTthis will be uncommented-->``)
        - Removing text between ``<!--$REMOVE-->`` and ``<!--$END_REMOVE-->``

    This is to enable translation between GitHub Markdown and MyST Markdown used
    in docs. For more details, see ``doc/README.md``.

    Args:
        source_path: The path to the locally saved markdown file to preprocess.
        dest_path: The destination path to save the preprocessed markdown file.
            If not provided, save to the same location as source_path.
    """
    dest_path = dest_path if dest_path else source_path
    with open(source_path, 'r') as f:
        text = f.read()
    # $UNCOMMENT
    text = re.sub(r'<!--\s*\$UNCOMMENT(.*?)(-->)', r'\1', text, flags=re.DOTALL)
    # $REMOVE
    text = re.sub(
        r'(<!--\s*\$REMOVE\s*-->)(.*?)(<!--\s*\$END_REMOVE\s*-->)',
        r'',
        text,
        flags=re.DOTALL,
    )

    with open(dest_path, 'w') as f:
        f.write(text)


@dataclasses.dataclass
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
    path: pathlib.Path
    category: str = None
    main_file: pathlib.Path = dataclasses.field(init=False)
    other_files: list[pathlib.Path] = dataclasses.field(init=False)
    title: str = dataclasses.field(init=False)

    def __post_init__(self):
        self.main_file = self.determine_main_file()
        self.other_files = self.determine_other_files()
        self.title = self.determine_title()

    def determine_main_file(self) -> pathlib.Path:
        """
        Determines the main file in the given path.
        If the path is a file, it returns the path itself. Otherwise, it searches
        for Markdown files (*.md) in the directory and returns the first one found.
        Returns:
            pathlib.Path: The main file path, either the original path if it's a file or the first
            Markdown file found in the directory.
        Raises:
            IndexError: If no Markdown files are found in the directory.
        """ # noqa: E501
        return self.path if self.path.is_file() else list(
            sorted(self.path.glob('*.md'),
                   key=lambda x: x.stem == 'README')).pop()

    def determine_other_files(self) -> list[pathlib.Path]:
        """
        Determine other files in the directory excluding the main file.

        This method checks if the given path is a file. If it is, it returns an empty list.
        Otherwise, it recursively searches through the directory and returns a list of all
        files that are not the main file and are tracked by git.

        Returns:
            list[pathlib.Path]: A list of pathlib.Path objects representing the other git-tracked files in the directory.
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
        def make_relative(path: pathlib.Path) -> pathlib.Path:
            return ROOT_DIR_RELATIVE / path.relative_to(ROOT_DIR)

        markdown_contents_dir = EXAMPLE_DOC_DIR / 'markdown_contents'
        markdown_contents_dir.mkdir(exist_ok=True)
        if self.main_file.suffix == '.md':
            markdown_contents_path = (markdown_contents_dir /
                                      self.path.name).with_suffix('.md')
            preprocess_github_markdown_file(self.main_file,
                                            markdown_contents_path)

        content = f'Source: <gh-file:{self.path.relative_to(ROOT_DIR)}>\n\n'
        if self.main_file.suffix == '.md':
            content += f':::{{include}} {make_relative(markdown_contents_path)}\n'
        else:
            content += f'# {self.title}\n\n'
            content += f':::{{literalinclude}} {make_relative(self.main_file)}\n'
            content += f':language: {self.main_file.suffix[1:]}\n'
        content += ':::\n\n'

        if not self.other_files:
            return content

        # Binary file extensions to skip
        BINARY_EXTENSIONS = {
            '.png', '.jpg', '.jpeg', '.gif', '.mp4', '.avi', '.mov', '.pdf',
            '.zip', '.tar', '.gz', '.bz2'
        }

        content += '## Included files\n\n'
        for file in sorted(self.other_files):
            # Skip binary files
            if file.suffix.lower() in BINARY_EXTENSIONS:
                continue

            include = 'include' if file.suffix == '.md' else 'literalinclude'
            content += f':::{{admonition}} {file.relative_to(self.path)}\n'
            content += ':class: dropdown\n\n'
            content += f':::{{{include}}} {make_relative(file)}\n'
            if include == 'literalinclude' and file.suffix in _SUFFIX_TO_LANGUAGE:
                content += f':language: {_SUFFIX_TO_LANGUAGE[file.suffix]}\n'
            content += ':::\n:::\n\n'

        return content


def generate_examples(app=None, *args, **kwargs):
    EXAMPLE_DOC_DIR.mkdir(parents=True, exist_ok=True)

    for example_dir in EXAMPLE_DIRS:
        _work(example_dir)


def _work(example_dir: pathlib.Path):
    examples = []

    # Find uncategorised examples
    globs = [example_dir.glob(pattern) for pattern in _GLOB_PATTERNS]
    for path in itertools.chain(*globs):
        examples.append(Example(path))

    # Find examples in subdirectories (up to 3 levels deep)
    for path in example_dir.glob("*/*.md"):
        examples.append(Example(path.parent))

    for path in example_dir.glob("*/*/*.md"):
        examples.append(Example(path.parent))

    # Check for stem collisions using full directory names
    stems = {}
    for example in examples:
        stem = example.path.stem
        if stem in stems and stems[stem] != example.path:
            raise ValueError(
                f'Collision detected: Multiple examples with stem "{stem}".\n'
                f'First path: {stems[stem]}\n'
                f'Second path: {example.path}\n'
                'Please rename one of them.')
        stems[stem] = example.path
        example.title = fix_case(stem.replace(
            '_', ' ').title())  # Update title accordingly

    # Generate the example documentation using the updated stem
    for example in sorted(examples, key=lambda e: e.path.name):
        doc_path = EXAMPLE_DOC_DIR / f'{example.path.stem}.md'
        with open(doc_path, 'w+') as f:
            f.write(example.generate())


if __name__ == '__main__':
    generate_examples()
