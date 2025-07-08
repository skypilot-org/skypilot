"""Tests for API compatibility between current payloads.py and master branch payloads.py.

This test suite dynamically fetches the latest payloads.py from the master branch on GitHub
and compares it with the current local version to detect any API incompatibilities.
"""
import importlib.util
import inspect
import re
import tempfile
from typing import Any, Dict, get_args, get_origin, Type, Union
import urllib.request

from pydantic import BaseModel
import pytest

# Group all tests in this file to run on the same worker to avoid conflicts when downloading master payloads
pytestmark = pytest.mark.xdist_group(name="pytest_api_compatibility")

from sky import serve
from sky.server import constants as current_constants
from sky.server.requests import payloads as current_payloads
from sky.utils import common as common_lib

# URLs to the master branch files
MASTER_PAYLOADS_URL = "https://raw.githubusercontent.com/skypilot-org/skypilot/master/sky/server/requests/payloads.py"
MASTER_CONSTANTS_URL = "https://raw.githubusercontent.com/skypilot-org/skypilot/master/sky/server/constants.py"

# Hint message for API compatibility failures
API_VERSION_HINT = "If this breaking change is intentional, please bump up API_VERSION in sky/server/constants.py."


def fetch_master_api_version() -> str:
    """Fetch the API version from the master branch constants.py."""
    # Download the master constants.py file
    with urllib.request.urlopen(MASTER_CONSTANTS_URL) as response:
        master_content = response.read().decode('utf-8')

    return parse_api_version(master_content)


def parse_api_version(content: str) -> str:
    """Parse the API version from the content."""
    # Extract API_VERSION using regex
    # Matches: API_VERSION = value (pylint enforced format)
    pattern = r"API_VERSION\s=\s(\d+)"
    match = re.search(pattern, content)

    if match:
        return match.group(1)
    else:
        raise ValueError("API_VERSION not found in constants.py")


def check_api_version_compatibility() -> bool:
    """Check if current and master API versions match."""
    # Parse instead of read the constants directly to avoid regression breaking
    # the test when the format on PR branch is changed.
    current_content = inspect.getsource(current_constants)
    current_version = parse_api_version(current_content)
    master_version = fetch_master_api_version()

    return current_version == master_version


def fetch_master_payloads_module() -> Any:
    """Fetch and load the master branch payloads.py as a module."""
    # Download the master payloads.py file
    with urllib.request.urlopen(MASTER_PAYLOADS_URL) as response:
        master_content = response.read().decode('utf-8')

    # Create a temporary file to store the master version
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py',
                                     delete=False) as temp_file:
        temp_file.write(master_content)
        temp_file_path = temp_file.name

    # Load the master version as a module
    spec = importlib.util.spec_from_file_location("master_payloads",
                                                  temp_file_path)
    master_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(master_module)

    return master_module


def get_request_body_classes(module) -> Dict[str, Type]:
    """Get all request body classes from a module."""
    return {
        name: cls
        for name, cls in inspect.getmembers(module)
        if (inspect.isclass(cls) and issubclass(cls, BaseModel) and
            name.endswith('Body') and name != 'RequestBody'
           )  # More specific filtering
    }


def get_field_info(model_class: Type) -> Dict[str, Dict[str, Any]]:
    """Get field information for a model class.

    Returns a dictionary mapping field names to their type information and default values.
    """
    field_info = {}
    for field_name, field in model_class.model_fields.items():
        field_info[field_name] = {
            'type': field.annotation,
            'default': field.default,
            'required': field.is_required()
        }
    return field_info


def compare_field_types(type1: Any, type2: Any) -> bool:
    """Compare two type annotations for compatibility."""
    # Handle Optional types
    if get_origin(type1) is Union and type(None) in get_args(type1):
        type1 = next(arg for arg in get_args(type1) if arg is not type(None))
    if get_origin(type2) is Union and type(None) in get_args(type2):
        type2 = next(arg for arg in get_args(type2) if arg is not type(None))

    # Handle List types
    if get_origin(type1) is list:
        type1 = get_args(type1)[0]
    if get_origin(type2) is list:
        type2 = get_args(type2)[0]

    # Compare the base types
    return type1 == type2


def generate_default_value(field_type: Any) -> Any:
    """Generate a default value for a given type annotation."""
    # Handle Optional types (Union with None)
    if get_origin(field_type) is Union:
        args = get_args(field_type)
        if type(None) in args:
            # It's Optional, return None for now, but we'll override with specific values later
            return None
        else:
            # It's a Union but not Optional, use first type
            field_type = args[0]

    # Handle basic types
    if field_type == str:
        return "test-string"
    elif field_type == int:
        return 42
    elif field_type == bool:
        return False
    elif field_type == float:
        return 1.0
    elif get_origin(field_type) is list:
        # Handle List[specific_type]
        args = get_args(field_type)
        if args:
            element_type = args[0]
            if element_type == str:
                return ["test-item"]
            elif element_type == int:
                return [1]
            else:
                return []
        return []
    elif get_origin(field_type) is dict:
        return {}
    elif get_origin(field_type) is tuple:
        return ()

    # Handle specific enum types by checking the string representation
    field_type_str = str(field_type)
    if 'OptimizeTarget' in field_type_str:
        return common_lib.OptimizeTarget.COST
    elif 'StatusRefreshMode' in field_type_str:
        return common_lib.StatusRefreshMode.NONE
    elif 'UpdateMode' in field_type_str:
        return serve.UpdateMode.ROLLING
    elif 'ServiceComponent' in field_type_str:
        return serve.ServiceComponent.CONTROLLER

    # Try to handle enum types by checking if it has enum-like attributes
    if hasattr(field_type, '__members__'):
        # It's likely an enum, return the first member
        try:
            return list(field_type.__members__.values())[0]
        except (AttributeError, IndexError):
            pass

    # Default fallback
    return None


def generate_test_data_for_class(model_class: Type) -> Dict[str, Any]:
    """Generate test data for a model class by inspecting its fields."""
    test_data = {}

    # Get field information
    if hasattr(model_class, 'model_fields'):
        for field_name, field in model_class.model_fields.items():
            # Check if field has a valid default value (not PydanticUndefined)
            if hasattr(field, 'default') and field.default is not None and str(
                    field.default) != 'PydanticUndefined':
                # Use the default value
                test_data[field_name] = field.default
            else:
                # Generate a test value based on the field type
                test_data[field_name] = generate_default_value(field.annotation)

    # Special handling for specific classes that need valid data
    class_name = model_class.__name__
    if 'Dag' in class_name or 'Launch' in class_name or 'Exec' in class_name:
        if 'dag' in test_data:
            test_data['dag'] = 'resources:\n  cpus: 1\nrun: echo hello'
        if 'task' in test_data:
            test_data['task'] = 'resources:\n  cpus: 1\nrun: echo hello'

    # Override with more specific test values for common fields
    # We need to be careful about field types - some fields with same name expect different types
    field_overrides = {
        'cluster_name': 'test-cluster',
        'service_name': 'test-service',
        'user_id': 'test-user',
        'role': 'admin',
        'name': 'test-name',
        'status': 'success',
        'config': {
            'key': 'value'
        },
        'workspace_name': 'test-workspace',
        'folder_paths': ['/test/path'],
        'request_ids': ['req-1', 'req-2'],
        'job_ids': ['1', '2', '3'],  # String list, not int list
        'cluster': 'test-cluster',
        'local_dir': '/tmp/logs',
        'idle_minutes': 60,
        'replica_id': 1,
        'job_id': 1,
        'ips': ['192.168.1.1', '192.168.1.2'],
        'ssh_user': 'test-user',
        'ssh_key': '/path/to/key',
        'context': 'test-context',
        'context_name': 'test-context',
        'password': 'test-password',
        'infra': 'test-infra',
        'log_path': '/path/to/log',
        'missing_chunks': ['chunk1', 'chunk2'],
        'service_names': ['service1', 'service2'],
        'targets': serve.ServiceComponent.CONTROLLER,
        'replica_ids': [1, 2],
    }

    # Apply field overrides
    for field_name, override_value in field_overrides.items():
        if field_name in test_data:
            test_data[field_name] = override_value

    # Handle specific model-based overrides that need different types for same field names
    class_name = model_class.__name__

    # Handle refresh field - different models expect different types
    if 'refresh' in test_data:
        if 'StatusBody' in class_name:
            test_data['refresh'] = common_lib.StatusRefreshMode.NONE
        else:
            # Most other models expect boolean for refresh
            test_data['refresh'] = False

    # Handle clouds field - some expect tuple, some list
    if 'clouds' in test_data:
        if 'CheckBody' in class_name:
            test_data['clouds'] = ('aws', 'gcp')  # Tuple
        else:
            test_data['clouds'] = ['aws', 'gcp']  # List

    # Handle target field - different models expect different types
    if 'target' in test_data:
        if 'ServeLogsBody' in class_name:
            test_data['target'] = serve.ServiceComponent.CONTROLLER
        else:
            test_data['target'] = 'controller'

    # Handle mode field
    if 'mode' in test_data:
        test_data['mode'] = serve.UpdateMode.ROLLING

    # Handle minimize/optimize_target fields
    if 'minimize' in test_data:
        test_data['minimize'] = common_lib.OptimizeTarget.COST
    if 'optimize_target' in test_data:
        test_data['optimize_target'] = common_lib.OptimizeTarget.COST

    return test_data


# Check API version compatibility first
API_VERSIONS_COMPATIBLE = check_api_version_compatibility()

# Load modules only if API versions are compatible
CURRENT_PAYLOADS_MODULE = current_payloads
MASTER_PAYLOADS_MODULE = fetch_master_payloads_module(
) if API_VERSIONS_COMPATIBLE else None

# Get all request body classes from both modules
CURRENT_PAYLOADS_CLASSES = get_request_body_classes(CURRENT_PAYLOADS_MODULE)
MASTER_PAYLOADS_CLASSES = get_request_body_classes(
    MASTER_PAYLOADS_MODULE) if MASTER_PAYLOADS_MODULE else {}

# Dynamically generate list of all request body classes to test
REQUEST_BODIES = list(CURRENT_PAYLOADS_CLASSES.keys())


def get_test_data_for_model(model_name: str) -> Dict[str, Any]:
    """Returns test data for a given model name."""
    # Get the model class and generate test data dynamically
    if model_name in CURRENT_PAYLOADS_CLASSES:
        return generate_test_data_for_class(
            CURRENT_PAYLOADS_CLASSES[model_name])

    # Fallback to empty dict if model not found
    return {}


@pytest.mark.skipif(
    not API_VERSIONS_COMPATIBLE,
    reason=
    "API versions don't match - skipping compatibility check as this indicates intentional breaking changes"
)
class TestAPICompatibility:
    """Test API compatibility between current payloads.py and master branch payloads.py."""

    @pytest.mark.parametrize('model_name', REQUEST_BODIES)
    def test_model_compatibility(self, model_name: str) -> None:
        """Test that models from current payloads.py are compatible with master branch payloads.py."""

        # Get the model classes
        current_model: Type = CURRENT_PAYLOADS_CLASSES[model_name]
        if model_name not in MASTER_PAYLOADS_CLASSES:
            # New models are allowed for API compatibility.
            pytest.skip(f"Model {model_name} not found in master payloads.py")
        master_model: Type = MASTER_PAYLOADS_CLASSES[model_name]

        # Get field information
        current_fields = get_field_info(current_model)
        master_fields = get_field_info(master_model)

        # Check for missing fields (fields removed from current, still exist in master)
        missing_in_current = set(master_fields.keys()) - set(
            current_fields.keys())
        if missing_in_current:
            pytest.fail(
                f"Fields removed from current {model_name} but still exist in master: {missing_in_current}. "
                f"{API_VERSION_HINT}")

        # Check for new fields (fields added to current)
        # New fields are allowed for API compatibility as long as they have default values.
        # This uses special handling as described in #5644: ensure new field has a default,
        # do not set the field for legacy server, and do not dump the field to request body if it equals default.
        extra_in_current = set(current_fields.keys()) - set(
            master_fields.keys())
        if extra_in_current:
            # Check that all new fields have default values (are not required)
            required_new_fields = [
                field_name for field_name in extra_in_current
                if current_fields[field_name]['required']
            ]
            if required_new_fields:
                pytest.fail(
                    f"New required fields in {model_name} break API compatibility: {required_new_fields}. "
                    f"New fields must have default values to maintain backward compatibility. "
                    f"{API_VERSION_HINT}")

        # Check field type compatibility for common fields
        for field_name in master_fields:
            if field_name in current_fields:
                master_type = master_fields[field_name]['type']
                current_type = current_fields[field_name]['type']

                if not compare_field_types(master_type, current_type):
                    pytest.fail(
                        f"Type mismatch in {model_name}.{field_name}: "
                        f"master={master_type} != current={current_type}. "
                        f"{API_VERSION_HINT}")

        # Test instance creation and compatibility
        test_data = get_test_data_for_model(model_name)

        # Create an instance using current payloads.py
        try:
            current_instance = current_model(**test_data)
        except Exception as e:
            pytest.fail(
                f"Failed to create {model_name} instance with current payloads: {str(e)}"
            )

        # Convert to dict and create an instance using master payloads.py
        dict_data = current_instance.model_dump()

        # Remove any fields that don't exist in master (for backward compatibility testing)
        master_dict_data = {
            k: v for k, v in dict_data.items() if k in master_fields
        }

        try:
            master_instance = master_model(**master_dict_data)
        except Exception as e:
            pytest.fail(
                f"Failed to create {model_name} instance with master payloads: {str(e)}"
            )

        # Compare the dictionaries (only common fields)
        current_dict = current_instance.model_dump()
        master_dict = master_instance.model_dump()

        # Check if values are compatible for common fields (allowing for type coercion)
        for key in master_dict:
            if key in current_dict:
                current_val, master_val = current_dict[key], master_dict[key]
                # Allow some type coercion (e.g., bool to int)
                if (current_val != master_val and
                        not (isinstance(current_val, bool) and isinstance(
                            master_val, int) and int(current_val) == master_val)
                        and not (isinstance(master_val, bool) and
                                 isinstance(current_val, int) and
                                 int(master_val) == current_val)):
                    pytest.fail(
                        f"Value mismatch for {model_name}.{key}: "
                        f"current={current_val} ({type(current_val)}) != master={master_val} ({type(master_val)})"
                    )

    def test_no_models_removed(self) -> None:
        """Test that no models were removed from the current version compared to master."""

        missing_models = set(MASTER_PAYLOADS_CLASSES.keys()) - set(
            CURRENT_PAYLOADS_CLASSES.keys())
        if missing_models:
            pytest.fail(
                f"Models removed from current version: {missing_models}. "
                f"{API_VERSION_HINT}")
