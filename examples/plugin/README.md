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
controller_wheel_path: dist

plugins:
- class: example_plugin.ExamplePlugin
  upload_to_controller: true
```

When `upload_to_controller` is set to `true` for any plugin:
1. All `.whl` files found in the directory specified in `controller_wheel_path` are uploaded to remote clusters via file mounts
2. The wheels are installed in the SkyPilot runtime environment on the cluster
3. The `plugins.yaml` is also uploaded to the cluster, filtered to only include plugins with `upload_to_controller: true`.

This allows your plugins to run on both the API server and on job/serve
controllers.

**Note:** You must build the wheel files yourself before configuring them in `plugins.yaml`. All `.whl` files in the specified directory will be uploaded. For example:
```bash
python -m build  # or python setup.py bdist_wheel
# This typically creates wheel files in the dist/ directory
```

### Configuration

The `plugins.yaml` schema supports the following top-level fields:

- `controller_wheel_path` (optional): Path to a directory containing prebuilt plugin wheel files (.whl). All `.whl` files in this directory will be uploaded to controllers. Required when any plugin has `upload_to_controller: true`. If no `.whl` files are found in the directory, nothing will be uploaded.

The `plugins.yaml` schema supports the following fields per plugin:

- `class` (required): The Python class path of the plugin (e.g., `module.ClassName`)
- `upload_to_controller` (optional): If `true`, the plugin will be uploaded and deployed to remote controllers. Requires `controller_wheel_path` to be specified at the top level.
- `parameters` (optional): Dictionary of parameters to pass to the plugin constructor
