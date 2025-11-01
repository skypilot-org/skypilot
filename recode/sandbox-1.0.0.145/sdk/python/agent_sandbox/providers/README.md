# Cloud Providers

This module provides cloud provider implementations for sandbox management. Currently supports Volcengine as a cloud provider.

## Supported Providers

### Volcengine

The Volcengine provider uses the Volcengine VEFAAS (Volcengine Function as a Service) API to manage sandbox instances.

#### Features

- Create sandbox instances
- Delete sandbox instances  
- Get sandbox details
- List all sandboxes

#### Configuration

```python
from agent_sandbox.providers import VolcengineProvider

provider = VolcengineProvider(
    access_key="yourAccessKeyId",
    secret_key="yourAccessKeySecret", 
    region="cn-beijing",  # optional, defaults to cn-beijing
    client_side_validation=True  # optional, defaults to True
)
```

#### Usage

Refer to the [examples/provider_volcengine.py](../../examples/provider_volcengine.py) for usage.


## Adding New Providers

To add a new cloud provider:

1. Create a new provider class that inherits from `BaseProvider`
2. Implement the required methods: `create_sandbox`, `delete_sandbox`, `get_sandbox`, `list_sandboxes`
3. Add the provider to the `__init__.py` file
4. Update this README with provider-specific documentation

