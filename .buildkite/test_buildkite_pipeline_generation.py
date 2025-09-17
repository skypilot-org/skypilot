"""This script tests the buildkite pipeline generation script.

It modifies the smoke test files to print the test name and return without
running the actual test code, then runs the pipeline generation script
and compares the output to the generated pipeline.

Some parameters in smoke tests requires credentials to setup, so we need to
run the tests with the credentials.

PYTHONPATH=$(pwd)/tests:$PYTHONPATH \
pytest -n 0 --dist no .buildkite/test_buildkite_pipeline_generation.py

"""

import os
import pathlib
import re
import subprocess

import pytest
import yaml


def _insert_test_tracers(content):
    """Matches any function definition starting with `def test_...(` possibly
    spanning multiple lines, and inserts print statements.

    1) print(function_name)
    2) If 'generic_cloud' is in the parameters, prints "generic_cloud: {generic_cloud}".
    3) return

    Each of these inserted lines is indented 4 spaces more than the
    function definition line.

    Caveats:
      • Very naive parameter parsing.
      • Splits by commas, then strips out type annotations and defaults.
      • If you have advanced signatures, you may need a more robust approach.
    """

    pattern = re.compile(r'^(\s*)(def\s+test_\w+\(.*?\)):\s*\n',
                         flags=re.MULTILINE | re.DOTALL)

    def replacer(match):
        base_indent = match.group(1)  # e.g. "    "
        signature = match.group(
            2)  # e.g. "def test_job_queue(generic_cloud: str, x=42)"

        # Indent our inserted lines 4 spaces beyond the function definition:
        deeper_indent = base_indent + '    '

        # Remove leading "def " so we can isolate function name + parameters
        # signature_no_def might be "test_job_queue(generic_cloud: str, x=42)"
        signature_no_def = signature[4:].strip()

        # Try splitting on the first "("
        try:
            func_name, raw_params = signature_no_def.split('(', 1)
            func_name = func_name.strip()
            # Remove trailing ")" if it exists
            if raw_params.endswith(')'):
                raw_params = raw_params[:-1]
            # Flatten newlines/spaces
            raw_params = re.sub(r'\s+', ' ', raw_params).strip()
        except ValueError:
            # If splitting fails, fallback
            func_name = signature_no_def
            raw_params = ''

        # --------------------------------------------------
        # Parse out parameter names (naively)
        # --------------------------------------------------
        # 1) Split on commas.
        # 2) For each piece, remove type annotations (":something")
        #    and default values ("=something").
        # 3) Strip off leading "*" or "**".
        # e.g. "generic_cloud: str" => "generic_cloud"
        #      "x=42" => "x"
        #      "**kwargs" => "kwargs"
        # --------------------------------------------------
        arg_list = []
        if raw_params:
            for piece in raw_params.split(','):
                piece = piece.strip()
                # Remove type annotations and defaults (split off first colon or equals)
                piece = re.split(r'[:=]', piece, 1)[0]
                # Remove leading "*" or "**"
                piece = piece.lstrip('*').strip()
                if piece:
                    arg_list.append(piece)

        # Build the lines to insert
        lines = []
        # Keep original definition line + colon
        lines.append(f'{base_indent}{signature}:')
        # 1) Print function name
        lines.append(
            f"{deeper_indent}print('\\n{func_name}\\n', file=sys.stderr, flush=True)"
        )
        # 2) Print generic_cloud if present
        if 'generic_cloud' in arg_list:
            lines.append(
                f"{deeper_indent}print(f'generic_cloud: {{generic_cloud}}', file=sys.stderr, flush=True)"
            )
        # 3) Return
        lines.append(f'{deeper_indent}return\n')

        return '\n'.join(lines)

    updated_content = pattern.sub(replacer, content)
    return 'import sys\n' + updated_content


def _extract_test_names_from_pipeline(pipeline_path):
    with open(pipeline_path, 'r') as f:
        pipeline = yaml.safe_load(f)

    test_names = set()
    for step in pipeline['steps']:
        command = step['command']
        # Extract test name from pytest command
        # e.g. "pytest tests/smoke_tests/test_basic.py::test_example_app --aws"
        assert '::' in command
        test_name = command.split('::')[-1].split()[
            0]  # Split on space to remove args
        test_names.add(test_name)

    return test_names


@pytest.mark.parametrize('args', [
    '',
    '--aws',
    '--gcp',
    '--azure',
    '--kubernetes',
    '--generic-cloud aws',
    '--generic-cloud gcp',
    '--managed-jobs',
    '--managed-jobs --serve',
    '--managed-jobs --aws',
])
def test_generate_same_as_pytest(args):
    # Get all test files from smoke_tests directory
    test_files = [
        f'tests/smoke_tests/{f}' for f in os.listdir('tests/smoke_tests')
        if f.endswith('.py') and f != 'test_quick_tests_core.py'
    ]

    pytest_tests = set()
    try:
        # Modify each test file to just print and return
        for test_file in test_files:
            with open(test_file, 'r') as f:
                content = f.read()

            modified_content = _insert_test_tracers(content)

            with open(test_file, 'w') as f:
                f.write(modified_content)

        # Get all test functions from pytest for all files
        pytest_output = subprocess.check_output(
            f'pytest ./tests/test_smoke.py {args}',
            stderr=subprocess.STDOUT,
            text=True,
            shell=True)
        pytest_tests = set(re.findall(r'test_\w+', pytest_output))

        # Generate pipeline and extract test functions using YAML parsing
        env = dict(os.environ)
        env['PYTHONPATH'] = f"{pathlib.Path.cwd()}/tests:" \
                            f"{env.get('PYTHONPATH', '')}"

        subprocess.run(
            ['python', '.buildkite/generate_pipeline.py', '--args', args],
            env=env,
            check=True)

        # Extract test names using YAML parsing
        pipeline_tests = _extract_test_names_from_pipeline(
            '.buildkite/pipeline_smoke_tests_release.yaml')

        # Compare the sets
        assert pytest_tests == pipeline_tests, \
            f'Mismatch between pytest tests {pytest_tests} and pipeline tests {pipeline_tests}'

    finally:
        # Restore original files using git
        subprocess.run(['git', 'reset', '--hard', 'HEAD'], check=True)
