# When using pod@namespace+context, rsync passes args as: {us} -l pod namespace+context
# We need to split the pod@namespace+context into pod, namespace and context
# For backward compatibility, we use + as the separator between namespace and context and add handling when context is not provided
shift
pod=$1
shift
echo "pod: $pod" >&2
encoded_namespace_context=$1
# Revert the encoded namespace+context to the original string.
namespace_context=$(echo "$encoded_namespace_context" | sed 's|%40|@|g' | sed 's|%3A|:|g' | sed 's|%2B|+|g' | sed 's|%2F|/|g')
echo "namespace_context: $namespace_context" >&2
namespace=$(echo $namespace_context | cut -d+ -f1)
echo "namespace: $namespace" >&2
context=$(echo $namespace_context | grep '+' >/dev/null && echo $namespace_context | cut -d+ -f2- || echo "")
echo "context: $context" >&2
context_lower=$(echo "$context" | tr '[:upper:]' '[:lower:]')
shift
if [ -z "$context" ] || [ "$context_lower" = "none" ]; then
    kubectl exec -i $pod -n $namespace -- "$@"
else
    kubectl exec -i $pod -n $namespace --context=$context -- "$@"
fi
