#!/bin/bash
# Busy-waits for parent_pid to exit, then SIGTERM the child processes of proc_pid and SIGKILL proc_pid.

parent_pid=$1
proc_pid=$2
remote=${3:-0}

while kill -s 0 ${parent_pid}; do sleep 1; done 

if [ ${remote} -eq 0 ]; then
    # This is to avoid the PIPE outputing to the console after being killed in next line.
    pkill -PIPE -P ${proc_pid}
fi
if [[ $OSTYPE == 'darwin'* ]]; then
    # MacOS do not have the ps --forest option, it is enough to do pkill here. (pkill
    # only kills the first level descendant of the process, not recursively find all
    # of the descendants.)
    pkill -TERM -P ${proc_pid}
else
    # Recursively gracefully kill (SIGTERM) all child processes of proc_pid.
    ps --forest -o pid -g $(ps -o sid= -p ${proc_pid}) | tail -n +2 | xargs kill -15
fi
# Wait the processes to gracefully exit
sleep 5
kill -9 ${proc_pid}
