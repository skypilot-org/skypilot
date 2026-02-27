# Example Plugins for SkyPilot API Server

## Usage

```bash
$ pip install .
$ cp plugins.yaml ~/.sky/plugins.yaml
$ sky api stop; sky api start
```

## Remote Controller Support

Plugins can be automatically deployed to remote controllers (jobs controller, serve
controller) by creating a separate `remote_plugins.yaml` file that specifies which
plugins should be uploaded to controllers.

### Setup

1. Create `~/.sky/plugins.yaml` for API server plugins with `controller_wheel_path`:

```yaml
controller_wheel_path: dist

plugins:
- class: example_plugin.ExamplePlugin
```

2. Create `~/.sky/remote_plugins.yaml` for remote controller plugins:

```yaml
plugins:
- class: example_plugin.ExamplePatchPlugin
```

When `remote_plugins.yaml` exists and contains plugins:
1. All `.whl` files found in the directory specified in `controller_wheel_path` (in `plugins.yaml`) are uploaded to remote clusters via file mounts
2. The wheels are installed in the SkyPilot runtime environment on the cluster
3. The `remote_plugins.yaml` config is uploaded to the cluster (as `plugins.yaml`)

This allows your plugins to run on both the API server (if specified in `plugins.yaml`) and on job/serve controllers (if specified in `remote_plugins.yaml`).

**Note:** You must build the wheel files yourself before configuring them in `plugins.yaml`. All `.whl` files in the specified directory will be uploaded. For example:
```bash
python -m build  # or python setup.py bdist_wheel
# This typically creates wheel files in the dist/ directory
```

### Configuration

The `plugins.yaml` schema supports the following top-level fields:

- `controller_wheel_path` (optional): Path to a directory containing prebuilt plugin wheel files (.whl). All `.whl` files in this directory will be uploaded to controllers. If no `.whl` files are found in the directory, nothing will be uploaded.

The `plugins.yaml` schema supports the following fields per plugin:

- `class` (required): The Python class path of the plugin (e.g., `module.ClassName`)
- `parameters` (optional): Dictionary of parameters to pass to the plugin constructor

The `remote_plugins.yaml` schema supports the following fields per plugin:

- `class` (required): The Python class path of the plugin (e.g., `module.ClassName`)
- `parameters` (optional): Dictionary of parameters to pass to the plugin constructor

### Environment Variables

You can customize the paths to these configuration files using environment variables:

- `SKYPILOT_SERVER_PLUGINS_CONFIG`: Path to `plugins.yaml` (default: `~/.sky/plugins.yaml`)
- `SKYPILOT_SERVER_REMOTE_PLUGINS_CONFIG`: Path to `remote_plugins.yaml` (default: `~/.sky/remote_plugins.yaml`)
