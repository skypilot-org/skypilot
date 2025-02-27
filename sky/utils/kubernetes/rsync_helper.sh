#!/bin/bash
# When using resource@namespace+context, rsync passes args as: {us} -l resource namespace+context
# We need to split the resource@namespace+context into resource, namespace and context
# For backward compatibility, we use + as the separator between namespace and context and add handling when context is not provided
shift
resource=$1
shift
echo "resource: $resource" >&2
encoded_namespace_context=$1
# Revert the encoded namespace+context to the original string.
namespace_context=$(echo "$encoded_namespace_context" | sed 's|%40|@|g' | sed 's|%3A|:|g' | sed 's|%2B|+|g' | sed 's|%2F|/|g')
echo "namespace_context: $namespace_context" >&2
namespace=$(echo $namespace_context | cut -d+ -f1)
echo "namespace: $namespace" >&2
context=$(echo $namespace_context | grep '+' >/dev/null && echo $namespace_context | cut -d+ -f2- || echo "")
echo "context: $context" >&2
context_lower=$(echo "$context" | tr '[:upper:]' '[:lower:]')

# Check if the resource is a pod or a deployment
if [[ "$resource" == *"/"* ]]; then
    # Format is resource_type/resource_name
    echo "Resource contains type: $resource" >&2
    resource_type=$(echo $resource | cut -d/ -f1)
    resource_name=$(echo $resource | cut -d/ -f2)
    echo "Resource type: $resource_type, Resource name: $resource_name" >&2
else
    # For backward compatibility, assume it's a pod
    resource_type="pod"
    resource_name=$resource
    echo "Assuming resource is a pod: $resource_name" >&2
fi

shift
if [ -z "$context" ] || [ "$context_lower" = "none" ]; then
    # If context is none, it means we are using incluster auth. In this case,
    # use need to set KUBECONFIG to /dev/null to avoid using kubeconfig file.
    kubectl exec -i $resource_type/$resource_name -n $namespace --kubeconfig=/dev/null -- "$@"
else
    kubectl exec -i $resource_type/$resource_name -n $namespace --context=$context -- "$@"
fi
