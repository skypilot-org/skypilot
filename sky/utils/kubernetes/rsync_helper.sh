#!/bin/bash
# We need to determine the pod, namespace and context from the args
# For backward compatibility, we use + as the separator between namespace and context and add handling when context is not provided
if [ "$1" = "-l" ]; then
    # -l pod namespace+context ...
    # used by normal rsync
    shift
    pod=$1
    shift
    encoded_namespace_context=$1
    shift # Shift past the encoded namespace+context
    echo "pod: $pod" >&2
    # Revert the encoded namespace+context to the original string.
    namespace_context=$(echo "$encoded_namespace_context" | sed 's|%40|@|g' | sed 's|%3A|:|g' | sed 's|%2B|+|g' | sed 's|%2F|/|g')
    echo "namespace_context: $namespace_context" >&2
else
    # pod@namespace+context ...
    # used by openrsync
    encoded_pod_namespace_context=$1
    shift # Shift past the pod@namespace+context
    pod_namespace_context=$(echo "$encoded_pod_namespace_context" | sed 's|%40|@|g' | sed 's|%3A|:|g' | sed 's|%2B|+|g' | sed 's|%2F|/|g')
    echo "pod_namespace_context: $pod_namespace_context" >&2
    pod=$(echo $pod_namespace_context | cut -d@ -f1)
    echo "pod: $pod" >&2
    namespace_context=$(echo $pod_namespace_context | cut -d@ -f2-)
    echo "namespace_context: $namespace_context" >&2
fi

namespace=$(echo $namespace_context | cut -d+ -f1)
echo "namespace: $namespace" >&2
context=$(echo $namespace_context | grep '+' >/dev/null && echo $namespace_context | cut -d+ -f2- || echo "")
echo "context: $context" >&2
context_lower=$(echo "$context" | tr '[:upper:]' '[:lower:]')
container="${SKYPILOT_K8S_EXEC_CONTAINER:-ray-node}"
echo "container: $container" >&2

# Check if the resource is a pod or a deployment (or other type)
if [[ "$pod" == *"/"* ]]; then
    # Format is resource_type/resource_name
    echo "Resource contains type: $pod" >&2
    resource_type=$(echo $pod | cut -d/ -f1)
    resource_name=$(echo $pod | cut -d/ -f2)
    echo "Resource type: $resource_type, Resource name: $resource_name" >&2
else
    # For backward compatibility or simple pod name, assume it's a pod
    resource_type="pod"
    resource_name=$pod
    echo "Assuming resource is a pod: $resource_name" >&2
fi

if [ -z "$context" ] || [ "$context_lower" = "none" ]; then
    # If context is none, it means we are using incluster auth. In this case,
    # we need to set KUBECONFIG to /dev/null to avoid using kubeconfig file.
    kubectl_cmd_base="kubectl exec \"$resource_type/$resource_name\" -n \"$namespace\" -c \"$container\" --kubeconfig=/dev/null --"
else
    kubectl_cmd_base="kubectl exec \"$resource_type/$resource_name\" -n \"$namespace\" -c \"$container\" --context=\"$context\" --"
fi

# Execute command on remote pod, waiting for rsync to be available first.
# The waiting happens on the remote pod, not locally, which is more efficient
# and reliable than polling from the local machine.
# We wrap the command in a bash script that waits for rsync, then execs the original command.
# Timeout after MAX_WAIT_TIME_SECONDS seconds.
MAX_WAIT_TIME_SECONDS=300
MAX_WAIT_COUNT=$((MAX_WAIT_TIME_SECONDS * 2))
# Use --norc --noprofile to prevent bash from sourcing startup files that might
# output to stdout and corrupt the rsync protocol. All debug output must go to
# stderr (>&2) to keep stdout clean for rsync communication.
eval "${kubectl_cmd_base% --} -i -- bash --norc --noprofile -c 'count=0; until which rsync >/dev/null 2>&1; do if [ \$count -ge $MAX_WAIT_COUNT ]; then echo \"Error when trying to rsync files to kubernetes cluster. Package installation may have failed.\" >&2; exit 1; fi; sleep 0.5; count=\$((count+1)); done; exec \"\$@\"' -- \"\$@\""
