# When using pod@namespace, rsync passes args as: {us} -l pod namespace context
shift
pod=$1
shift
namespace=$1
shift
context=$1
shift
kubectl exec -i $pod -n $namespace --context=$context -- "$@"