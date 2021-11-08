"""
Utilities to help provisioners
"""
import shlex
import subprocess
import sys


def run_shell_command(command: str,
                      stream_output: bool = True):
    """
    Runs a shell command and waits for it to complete. This is a blocking call.
    :param stream_output: Whether to stream the output to the terminal while the command is running
    """
    process = subprocess.Popen(shlex.split(command), shell=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    all_output = ""
    # Poll process.stdout to show stdout live
    while True:
        output = process.stdout.readline()
        if output:
            if stream_output:
                sys.stdout.write(output)
            all_output += output
        if process.poll() is not None:
            break
    retcode = process.poll()
    return retcode, all_output

if __name__ == '__main__':
    command='ping 127.0.0.1 -c 4'
    r,o = run_shell_command(command)