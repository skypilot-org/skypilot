"""Utils to check if ssh control master should be disabled."""
from subprocess import CalledProcessError
from sky.utils import subprocess_utils


def is_tmp_9p_filesystem() -> bool:
    """Check if the /tmp filesystem is 9p.

    Returns:
        bool: True if the /tmp filesystem is 9p, False otherwise.
    """
    try:
        result = subprocess_utils.run(['df', '-T', '/tmp'],
                                      capture_output=True,
                                      text=True)

        if result.returncode != 0:
            raise CalledProcessError(result.returncode, result.args,
                                     result.stdout, result.stderr)

        filesystem_info = result.stdout.strip().split('\n')[1]
        filesystem_type = filesystem_info.split()[1]
        return filesystem_type.lower() == '9p'

    except CalledProcessError as e:
        print(f'Error running "df" command: {e}')

    return False


def disable_control_master_checks() -> bool:
    """Disable ssh control master checks if the /tmp filesystem is 9p.

    Returns:
        bool: True if the ssh control master should be disabled,
        False otherwise.
    """
    if is_tmp_9p_filesystem():
        return True
    # there may be additional criteria to disable ssh control master
    # in the future. They should be checked here
    return False
