# Example Plugins for SkyPilot API Server

## Usage

```bash
$ pip install .
$ cp plugins.yaml ~/.sky/plugins.yaml
$ sky api stop; sky api start
```

## Remote Controller Support

Plugins can be automatically deployed to remote controllers (jobs controller, serve
controller) by setting `upload_to_controller: true` in `plugins.yaml`:

```yaml
plugins:
- class: example_plugin.ExamplePlugin
  upload_to_controller: true
```

When `upload_to_controller` is set to `true`:
1. The plugin package path is automatically determined from the plugin module's location
2. The plugin package is built as a wheel locally
3. The wheel is uploaded to remote clusters via file mounts
4. The wheel is installed in the SkyPilot runtime environment on the cluster
5. The `plugins.yaml` is also uploaded to the cluster, filtered to only include plugins with `upload_to_controller: true`.

This allows your plugins to run on both the API server and on job/serve
controllers.

The package path is determined by walking up from the plugin module's file location
until a directory containing `pyproject.toml` or `setup.py` is found.

### Configuration

The `plugins.yaml` schema supports the following fields per plugin:

- `class` (required): The Python class path of the plugin (e.g., `module.ClassName`)
- `upload_to_controller` (optional): If `true`, the plugin package will be built and deployed to remote controllers. The package path is automatically determined from the module's location.
- `parameters` (optional): Dictionary of parameters to pass to the plugin constructor
