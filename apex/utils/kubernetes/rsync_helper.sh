# When using pod@namespace, rsync passes args as: {us} -l pod namespace 
shift
pod=$1
shift
namespace=$1
shift
kubectl exec -i $pod -n $namespace -- "$@"
