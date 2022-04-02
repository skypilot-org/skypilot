#!/bin/bash
# Busy-waits for parent_pid to exit, then SIGTERM the child processes of proc_pid and SIGKILL proc_pid.

parent_pid=$1
proc_pid=$2

while kill -s 0 ${parent_pid}; do sleep 1; done 

pkill -TERM -P ${proc_pid}
# Wait the processes to gracefully exit
sleep 5
kill -9 ${proc_pid}
