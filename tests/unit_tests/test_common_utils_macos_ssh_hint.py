"""Tests for the macOS SSH client hint helper in ``sky.utils.common_utils``."""
from unittest import mock

from sky.utils import common_utils


def _clear_cache():
    common_utils._macos_system_ssh_is_default.cache_clear()


def test_hint_empty_on_non_macos():
    _clear_cache()
    with mock.patch('sky.utils.common_utils.sys.platform', 'linux'):
        assert common_utils.maybe_macos_ssh_hint() == ''
        assert common_utils.maybe_macos_ssh_hint(
            'ssh: kex_exchange_identification: ...') == ''
    _clear_cache()


def test_hint_empty_when_brew_ssh_on_path():
    _clear_cache()
    with mock.patch('sky.utils.common_utils.sys.platform', 'darwin'), \
         mock.patch('sky.utils.common_utils.shutil.which',
                    return_value='/opt/homebrew/bin/ssh'), \
         mock.patch('sky.utils.common_utils.os.path.realpath',
                    return_value='/opt/homebrew/Cellar/openssh/9.9/bin/ssh'):
        assert common_utils.maybe_macos_ssh_hint() == ''
        assert common_utils.maybe_macos_ssh_hint('max_client exceeded') == ''
    _clear_cache()


def test_hint_returned_on_macos_with_system_ssh_and_no_stderr():
    _clear_cache()
    with mock.patch('sky.utils.common_utils.sys.platform', 'darwin'), \
         mock.patch('sky.utils.common_utils.shutil.which',
                    return_value='/usr/bin/ssh'), \
         mock.patch('sky.utils.common_utils.os.path.realpath',
                    return_value='/usr/bin/ssh'):
        hint = common_utils.maybe_macos_ssh_hint()
        assert 'brew install openssh' in hint
        assert 'sky api stop' in hint
    _clear_cache()


def test_hint_returned_on_macos_for_matching_stderr():
    _clear_cache()
    with mock.patch('sky.utils.common_utils.sys.platform', 'darwin'), \
         mock.patch('sky.utils.common_utils.shutil.which',
                    return_value='/usr/bin/ssh'), \
         mock.patch('sky.utils.common_utils.os.path.realpath',
                    return_value='/usr/bin/ssh'):
        for stderr in [
                'ssh: max_client exceeded',
                'kex_exchange_identification: Connection closed by remote host',
                'Too Many Authentication Failures',
                'Connection reset by peer',
                'client_loop: send disconnect: Broken pipe',
                'ssh_dispatch_run_fatal: some fatal',
        ]:
            hint = common_utils.maybe_macos_ssh_hint(stderr)
            assert 'brew install openssh' in hint, (
                f'Expected hint for stderr={stderr!r}, got {hint!r}')
    _clear_cache()


def test_hint_empty_on_macos_for_unrelated_stderr():
    _clear_cache()
    with mock.patch('sky.utils.common_utils.sys.platform', 'darwin'), \
         mock.patch('sky.utils.common_utils.shutil.which',
                    return_value='/usr/bin/ssh'), \
         mock.patch('sky.utils.common_utils.os.path.realpath',
                    return_value='/usr/bin/ssh'):
        assert common_utils.maybe_macos_ssh_hint(
            'Permission denied (publickey)') == ''
        assert common_utils.maybe_macos_ssh_hint(
            'Host key verification failed') == ''
    _clear_cache()
