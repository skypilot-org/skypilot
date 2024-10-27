from sky.utils import subprocess_utils
from subprocess import CalledProcessError

def is_tmp_9p_filesystem():
    """
    Check if the /tmp filesystem is 9p.
    """
    try:
        result = subprocess_utils.run(
            ['df', '-T', '/tmp'], capture_output=True, text=True
        )
        
        if result.returncode != 0:
            raise CalledProcessError(
                result.returncode, result.args, result.stdout, result.stderr
            )

        filesystem_info = result.stdout.strip().split('\n')[1]
        filesystem_type = filesystem_info.split()[1]
        return filesystem_type.lower() == '9p'

    except CalledProcessError as e:
        print(f"Error running 'df' command: {e}")
    except Exception as e:
        print(f"Unexpected error: {e}")
    
    return False

def disable_control_master_checks(): 
    """
    Disable ssh control master checks if the /tmp filesystem is 9p.
    """
    if is_tmp_9p_filesystem():
        return True
    # there may be additional criteria to disable ssh control master in the future. They should be checked here
    return False
