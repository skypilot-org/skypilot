# When using pod@namespace+context, rsync passes args as: {us} -l pod namespace+context
# We need to split the pod@namespace+context into pod, namespace and context
# For backward compatibility, we use + as the separator between namespace and context and add handling when context is not provided
shift
pod=$1
shift
namespace_context=$1
namespace=$(echo $namespace_context | cut -d+ -f1)
context=$(echo $namespace_context | grep '+' >/dev/null && echo $namespace_context | cut -d+ -f2- || echo "")
context_lower=$(echo "$context" | tr '[:upper:]' '[:lower:]')
shift
if [ -z "$context" ] || [ "$context_lower" = "none" ]; then
    kubectl exec -i $pod -n $namespace -- "$@"
else
    kubectl exec -i $pod -n $namespace --context=$context -- "$@"
fi
