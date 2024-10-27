import subprocess

def is_tmp_9p_filesystem():
    try:
        result = subprocess.run(['df', '-T', '/tmp'], capture_output=True, text=True)
        
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)

        filesystem_info = result.stdout.strip().split('\n')[1]
        filesystem_type = filesystem_info.split()[1]
        return filesystem_type.lower() == '9p'

    except subprocess.CalledProcessError as e:
        print(f"Error running 'df' command: {e}")
    except Exception as e:
        print(f"Unexpected error: {e}")
    
    return False

def disable_control_master_checks(): 
    if is_tmp_9p_filesystem():
        return True
    # there may be additional criteria to disable ssh control master in the future. They should be checked here
    return False
