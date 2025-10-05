# Workspaces

Workspaces are a way to group clusters/jobs together. A workspace could contain different cloud credentials and different configurations.


## Workspace verification

For each cluster creation / stop / down request, the active workspace is verified against the workspace of the cluster, and will raise an error if they are different.

The jobs controller will always be launched in the default workspace, and the managed jobs will be set to active workspace based on user's client settings.
