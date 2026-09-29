"""Execute both shipped SSH setup blocks without SSH or account mutations."""
import ast
import os
from pathlib import Path
import pwd
import shlex
import subprocess

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_LOOKUP = "$(unset HOME && builtin printf '%s\\n' ~)"


def _replace_once(text, old, new):
    count = text.count(old)
    if count != 1:
        raise ValueError(
            f'Expected exactly one fixture replacement, found {count}')
    return text.replace(old, new, 1)


def _extract_block(text, start, end):
    if text.count(start) != 1 or text.count(end) != 1:
        raise ValueError('Expected exactly one of each SSH block delimiter')
    remainder = text.partition(start)[2]
    block, separator, _ = remainder.partition(end)
    if not separator:
        raise ValueError('SSH block end precedes start')
    return block


@pytest.fixture(params=['template', 'legacy'])
def auth_script(request, tmp_path):
    if request.param == 'template':
        text = (_ROOT / 'sky/templates/kubernetes-ray.yml.j2').read_text()
        block = _extract_block(text,
                               'cd /etc/ssh/ && $(prefix_cmd) ssh-keygen -A;',
                               '# Start sshd portably:')
        block = '\n'.join(line.strip() for line in block.splitlines())
        return _replace_once(block, 'skypilot:ssh_public_key_content',
                             'fixture-key')
    tree = ast.parse(
        (_ROOT / 'sky/provision/kubernetes/instance.py').read_text())
    assignment = next(
        node for node in ast.walk(tree) if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == 'install_ssh_k8s_cmd'
            for target in node.targets))
    text = ast.literal_eval(assignment.value)
    block = _extract_block(text, 'cd /etc/ssh/ && $(prefix_cmd) ssh-keygen -A;',
                           '$(prefix_cmd) service ssh restart;')
    public_key = tmp_path / 'public-key'
    block = _replace_once(block, '/etc/secret-volume/ssh-publickey*',
                          shlex.quote(str(public_key)))
    public_key.write_text('fixture-key\n')
    return block


def _run(script, image_home, *, path=None):
    env = {**os.environ, 'HOME': str(image_home)}
    if path is not None:
        env['PATH'] = path
    return subprocess.run(
        ['/bin/bash', '--noprofile', '--norc', '-c', 'set -e\n' + script],
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
        check=False)


def test_real_passwd_lookup_preserves_image_home_without_external_tools(
        auth_script, tmp_path):
    # All directory operations are recorders. Never write the real account's
    # SSH directory; only change the key redirection to /dev/null.
    script = _replace_once(auth_script,
                           '> "$skypilot_ssh_home/.ssh/authorized_keys"',
                           '> /dev/null')
    stubs = '''
prefix_cmd() { :; }
whoami() { builtin printf 'fixture-user\n'; }
mkdir() { builtin printf '%s\n' "$2"; }
chown() { :; }
chmod() { :; }
cat() { :; }
'''
    result = _run(stubs + script + '\nbuiltin printf "%s\\n" "$HOME"',
                  tmp_path,
                  path='/no-external-tools')
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        str(Path(pwd.getpwuid(os.getuid()).pw_dir) / '.ssh'),
        str(tmp_path)
    ]


@pytest.mark.parametrize('username', ['root', 'sky'])
@pytest.mark.parametrize('matching_home', [False, True])
def test_auth_directory_key_and_modes(auth_script, tmp_path, username,
                                      matching_home):
    # Only the account lookup is synthetic; execute mkdir, key redirection and
    # chmod from the shipped block. Record chown instead of changing ownership.
    account_home = tmp_path / 'account home with spaces'
    image_home = account_home if matching_home else tmp_path / 'image-home'
    script = _replace_once(auth_script, _LOOKUP, shlex.quote(str(account_home)))
    stubs = f'''
prefix_cmd() {{ :; }}
whoami() {{ builtin printf '%s\\n' {shlex.quote(username)}; }}
chown() {{ builtin printf '%s\\n' "$@"; }}
'''
    result = _run(stubs + script + '\nbuiltin printf "%s\\n" "$HOME"',
                  image_home)
    assert result.returncode == 0, result.stderr
    auth_dir = account_home / '.ssh'
    key = auth_dir / 'authorized_keys'
    assert key.read_text() == 'fixture-key\n'
    assert auth_dir.stat().st_mode & 0o777 == 0o700
    assert key.stat().st_mode & 0o777 == 0o644
    assert result.stdout.splitlines() == [
        '-R', username, str(auth_dir),
        str(image_home)
    ]
    if not matching_home:
        assert not (image_home / '.ssh').exists()


@pytest.mark.parametrize('account_home', ['', '~', 'relative/home'])
def test_missing_or_invalid_account_home_fails_before_mutation(
        auth_script, tmp_path, account_home):
    script = _replace_once(auth_script, _LOOKUP, shlex.quote(account_home))
    stubs = '''
prefix_cmd() { :; }
mkdir() { builtin printf 'unexpected mkdir\n'; exit 99; }
chown() { builtin printf 'unexpected chown\n'; exit 99; }
chmod() { builtin printf 'unexpected chmod\n'; exit 99; }
'''
    result = _run(stubs + script, tmp_path)
    assert result.returncode == 1
    assert 'Cannot determine absolute SSH account home' in result.stderr
    assert not result.stdout
    assert not (tmp_path / '.ssh').exists()


def test_readonly_home_fails_before_mutation(auth_script, tmp_path):
    stubs = '''
readonly HOME
prefix_cmd() { :; }
mkdir() { builtin printf 'unexpected mkdir\n'; exit 99; }
'''
    result = _run(stubs + auth_script, tmp_path)
    assert result.returncode == 1
    assert 'HOME: cannot unset: readonly variable' in result.stderr
    assert not result.stdout
    assert not (tmp_path / '.ssh').exists()


def test_authorized_key_write_failure_aborts(auth_script, tmp_path):
    account_home = tmp_path / 'account-home'
    script = _replace_once(auth_script, _LOOKUP, shlex.quote(str(account_home)))
    key = account_home / '.ssh/authorized_keys'
    key.mkdir(parents=True)
    stubs = '''
prefix_cmd() { :; }
whoami() { builtin printf 'fixture-user\n'; }
chown() { :; }
'''
    result = _run(stubs + script + '\necho setup-complete',
                  tmp_path / 'image-home')
    assert result.returncode != 0
    assert 'authorized_keys' in result.stderr
    assert not result.stdout
    assert key.is_dir()


@pytest.mark.parametrize('matches', [0, 2])
@pytest.mark.parametrize('harness', ['redirect', 'lookup', 'write_failure'])
def test_inexact_substitution_never_executes_or_creates_auth_directory(
        auth_script, tmp_path, monkeypatch, matches, harness):
    target = ('> "$skypilot_ssh_home/.ssh/authorized_keys"'
              if harness == 'redirect' else _LOOKUP)
    script = _replace_once(auth_script, target, target * matches)

    def forbidden(*args, **kwargs):
        pytest.fail(
            'Fixture substitution failed to prevent subprocess execution')

    monkeypatch.setattr(subprocess, 'run', forbidden)
    with pytest.raises(ValueError,
                       match='Expected exactly one fixture replacement'):
        if harness == 'redirect':
            test_real_passwd_lookup_preserves_image_home_without_external_tools(
                script, tmp_path)
        elif harness == 'lookup':
            test_auth_directory_key_and_modes(script, tmp_path, 'root', False)
        else:
            test_authorized_key_write_failure_aborts(script, tmp_path)
    assert not (tmp_path / 'account-home').exists()
    assert not (tmp_path / 'account home with spaces').exists()


@pytest.mark.parametrize('text', [
    'start body', 'body end', 'start start body end', 'start body end end',
    'end body start'
])
def test_invalid_block_delimiters_never_execute(text, tmp_path, monkeypatch):

    def forbidden(*args, **kwargs):
        pytest.fail('Invalid SSH block extraction reached subprocess execution')

    monkeypatch.setattr(subprocess, 'run', forbidden)
    with pytest.raises(ValueError):
        _run(_extract_block(text, 'start', 'end'), tmp_path)
