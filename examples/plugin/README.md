# Example Plugins for SkyPilot API Server

## Local API Server Usage

```bash
$ pip install .
$ cp plugins.yaml ~/.sky/plugins.yaml
$ sky api stop; sky api start
```

## Remote Cluster Support

Plugins can be automatically deployed to remote clusters (jobs controller, serve
controller) by specifying the `package` field in `plugins.yaml`:

```yaml
plugins:
- class: example_plugin.ExamplePlugin
  package: ~/path/to/plugin/directory
```

When a `package` path is specified:
1. The plugin package is built as a wheel locally
2. The wheel is uploaded to remote clusters via file mounts
3. The wheel is installed in the SkyPilot runtime environment on the cluster
4. The `plugins.yaml` is also uploaded to the cluster

This allows your plugins to run on both the API server and on job/serve
controllers.

### Configuration

The `plugins.yaml` schema supports the following fields per plugin:

- `class` (required): The Python class path of the plugin (e.g., `module.ClassName`)
- `package` (optional): Path to the plugin package directory containing `pyproject.toml` or `setup.py`. If specified, the package will be built and deployed to remote clusters.
- `parameters` (optional): Dictionary of parameters to pass to the plugin constructor
