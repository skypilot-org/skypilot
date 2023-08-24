# Cluster Name Mapping

## Background

SkyPilot allows users to assign names to clusters they launched. It is convenient, but
when the following situation happens, it can be annoying:
1. The cluster name exceeds clouds' naming length limit.
2. Multiple users in the same cloud account organization use the same cluster name.

## Solution

After #2403, SkyPilot will automatically truncate cluster names to comply with the
naming length limit of the cloud, and append a user hash to the end of the name to
distinguish clusters from different users (we make sure the final result after the append is within length limit).

## Details

Cluster names used in different situations:
- CLI/API/UI: the name users assign to the cluster, i.e., the original cluster name
    without any modification. `cluster_name` in code.
- Lower-level components: for the components that directly interact with the cloud
    provider, the newly generated name will be used, including, the underlying
    `ray up`, `provision` module, etc. `cluster_name_on_cloud` in code.
- Cluster name on cloud: names of clusters on the cloud will always be
    `cluster_name_on_cloud`, including the cluster name shown in cloud consoles,
    the cluster name in the tag (ray-cluster-name), etc.

## Examples

For a cluster name less than cloud's naming length limit, the name will only be appended
with a user hash.
```
# Original
my-cluster-name

# Generated
my-cluster-name-9eac
```

For a cluster name longer than cloud's naming length limit, the name will be truncated
to less than the limit, appended with a cluster name hash, and then appended with a user hash.
```
# Original
my-cluster-name-very-long

# Generated
my-cluster-name-3m-9eac
```
