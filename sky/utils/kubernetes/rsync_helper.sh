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
    kubectl_cmd_base="kubectl exec \"$resource_type/$resource_name\" -n \"$namespace\" --kubeconfig=/dev/null --"
else
    kubectl_cmd_base="kubectl exec \"$resource_type/$resource_name\" -n \"$namespace\" --context=\"$context\" --"
fi

# Check if rsync is available, if not wait. Rsync installation happens 
# asynchronously during pod startup, so we need to wait for it to be available.
wait_for_rsync() {
    # Check if rsync is available.
    if timeout 5s bash -c "eval \"$kubectl_cmd_base\" which rsync" >/dev/null 2>&1 </dev/null; then
        echo "Worked on first check" >&2
        return 0
    fi
    
    # If not available, wait 60 seconds for installation to complete
    echo "rsync not found, waiting 60s for installation to complete..." >&2
    sleep 60
    
    # Check again after waiting
    if timeout 5s bash -c "eval \"$kubectl_cmd_base\" which rsync >/dev/null 2>&1 </dev/null"; then
        echo "Worked on second check" >&2
        return 0
    fi
    
    echo "Error: rsync not available after waiting 60s, package installation is either slow or failed.." >&2
    return 1
}
wait_for_rsync

# Insert -i flag before the -- separator and execute
eval "${kubectl_cmd_base% --} -i -- \"\$@\""