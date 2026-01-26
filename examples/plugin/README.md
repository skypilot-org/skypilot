# Example Plugins for SkyPilot API Server

## Usage

```bash
$ pip install .
$ cp plugins.yaml ~/.sky/plugins.yaml
$ sky api stop; sky api start
```

## Remote Controller Support

Plugins can be automatically deployed to remote controllers (jobs controller, serve
controller) by setting `upload_to_controller: true` and providing a `controller_wheel_path` at the top level of `plugins.yaml`:

```yaml
controller_wheel_path: dist/example_plugin-0.0.1-py3-none-any.whl

plugins:
- class: example_plugin.ExamplePlugin
  upload_to_controller: true
```

When `upload_to_controller` is set to `true` for any plugin:
1. The prebuilt wheel file specified in `controller_wheel_path` (containing all plugins) is uploaded to remote clusters via file mounts
2. The wheel is installed in the SkyPilot runtime environment on the cluster
3. The `plugins.yaml` is also uploaded to the cluster, filtered to only include plugins with `upload_to_controller: true`.

This allows your plugins to run on both the API server and on job/serve
controllers.

**Note:** You must build the wheel file yourself before configuring it in `plugins.yaml`. The wheel should contain all plugins that will be uploaded to controllers. For example:
```bash
python -m build  # or python setup.py bdist_wheel
```

### Configuration

The `plugins.yaml` schema supports the following top-level fields:

- `controller_wheel_path` (optional): Path to a prebuilt plugin wheel file (.whl) containing all plugins that will be uploaded to controllers. Required when any plugin has `upload_to_controller: true`.

The `plugins.yaml` schema supports the following fields per plugin:

- `class` (required): The Python class path of the plugin (e.g., `module.ClassName`)
- `upload_to_controller` (optional): If `true`, the plugin will be uploaded and deployed to remote controllers. Requires `controller_wheel_path` to be specified at the top level.
- `parameters` (optional): Dictionary of parameters to pass to the plugin constructor
