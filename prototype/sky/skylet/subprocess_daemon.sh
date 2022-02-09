#!/bin/bash

parent_pid=$1
proc_pid=$2

while kill -s 0 ${parent_pid}; do sleep 1; done 

pkill -TERM -P ${proc_pid}
kill -9 ${proc_pid}
