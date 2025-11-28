#!/usr/bin/env python3
"""
Patch buildkite-test-collector package to add detailed upload logging.

This script patches the installed buildkite-test-collector package at runtime
to add detailed logging for debugging upload issues in CI environments.
"""

import importlib.util
import os
import re
import site
import sys


def find_buildkite_collector_package():
    """Find the installed buildkite-test-collector package location."""
    try:
        import buildkite_test_collector
        package_path = os.path.dirname(buildkite_test_collector.__file__)
        return package_path
    except ImportError:
        # Try to find it manually in site-packages
        for site_packages_dir in site.getsitepackages():
            collector_path = os.path.join(site_packages_dir,
                                          'buildkite_test_collector')
            if os.path.exists(collector_path):
                return collector_path
        raise RuntimeError("Could not find buildkite-test-collector package")


def patch_api_file(api_file_path):
    """Patch the API file to add detailed upload logging."""
    with open(api_file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Check if already patched
    if 'BUILDKITE TEST COLLECTOR UPLOAD DEBUG START' in content:
        print(f"API file already patched: {api_file_path}")
        return

    # Find the submit method and replace it
    old_submit = '''    def submit(self, payload: Payload, batch_size=100) -> Generator[Optional[Response], Any, Any]:
        """Submit a payload to the API"""
        response = None

        if not self.ci:
            yield None

        if not self.token:
            logger.warning("No %s environment variable present", self.ENV_TOKEN)
            yield None

        else:
            for payload_slice in payload.into_batches(batch_size):
                try:
                    response = post(self.api_url + "/uploads",
                                    json=payload_slice.as_json(),
                                    headers={
                                        "Content-Type": "application/json",
                                        "Authorization": f"Token token=\\"{self.token}\\""
                                    },
                                    timeout=60)
                    response.raise_for_status()
                    yield response
                except InvalidHeader as error:
                    logger.warning("Invalid %s environment variable", self.ENV_TOKEN)
                    logger.warning(error)
                    yield None
                except HTTPError as err:
                    logger.warning("Failed to uploads test results to buildkite")
                    logger.warning(err)
                    yield None
                except Exception:  # pylint: disable=broad-except
                    error_message = traceback.format_exc()
                    logger.warning(error_message)
                    yield None'''

    new_submit = '''    def submit(self, payload: Payload, batch_size=100) -> Generator[Optional[Response], Any, Any]:
        """Submit a payload to the API"""
        response = None

        logger.info("=== BUILDKITE TEST COLLECTOR UPLOAD DEBUG START ===")
        logger.info(f"CI environment variable: {self.ci}")
        logger.info(f"BUILDKITE_ANALYTICS_TOKEN present: {bool(self.token)}")
        logger.info(f"BUILDKITE_ANALYTICS_TOKEN length: {len(self.token) if self.token else 0}")
        logger.info(f"API URL: {self.api_url}")

        payload_json = payload.as_json()
        payload_data = payload_json.get("data", [])
        logger.info(f"Payload contains {len(payload_data)} test(s)")
        logger.info(f"Payload JSON size: {len(str(payload_json))} characters")

        if not self.ci:
            logger.warning("CI environment variable is not set, skipping upload")
            logger.info("=== BUILDKITE TEST COLLECTOR UPLOAD DEBUG END (CI not set) ===")
            yield None
            return

        if not self.token:
            logger.warning("No %s environment variable present", self.ENV_TOKEN)
            logger.info("=== BUILDKITE TEST COLLECTOR UPLOAD DEBUG END (no token) ===")
            yield None
            return

        else:
            batches = list(payload.into_batches(batch_size))
            logger.info(f"Payload will be split into {len(batches)} batch(es)")

            for batch_idx, payload_slice in enumerate(batches):
                try:
                    upload_url = self.api_url + "/uploads"
                    payload_json_data = payload_slice.as_json()
                    payload_size = len(str(payload_json_data))
                    num_tests = len(payload_json_data.get("data", []))

                    logger.info(f"=== Uploading batch {batch_idx + 1}/{len(batches)} ===")
                    logger.info(f"Upload URL: {upload_url}")
                    logger.info(f"Batch payload size: {payload_size} characters")
                    logger.info(f"Batch contains {num_tests} test(s)")
                    logger.info(f"Authorization header: Token token=\\"***\\" (token length: {len(self.token)})")

                    response = post(upload_url,
                                    json=payload_json_data,
                                    headers={
                                        "Content-Type": "application/json",
                                        "Authorization": f"Token token=\\"{self.token}\\""
                                    },
                                    timeout=60)

                    logger.info(f"HTTP Status Code: {response.status_code}")
                    logger.info(f"Response Headers: {dict(response.headers)}")
                    logger.info(f"Response Body: {response.text[:500]}")  # First 500 chars

                    response.raise_for_status()
                    logger.info(f"✓ Successfully uploaded batch {batch_idx + 1}/{len(batches)}")
                    yield response
                except InvalidHeader as error:
                    logger.error("Invalid %s environment variable", self.ENV_TOKEN)
                    logger.error(f"Error details: {error}")
                    logger.error(f"Error type: {type(error).__name__}")
                    yield None
                except HTTPError as err:
                    logger.error("Failed to upload test results to buildkite")
                    logger.error(f"HTTP Error: {err}")
                    logger.error(f"Response status: {err.response.status_code if err.response else 'N/A'}")
                    logger.error(f"Response text: {err.response.text if err.response else 'N/A'}")
                    logger.error(f"Response headers: {dict(err.response.headers) if err.response else 'N/A'}")
                    yield None
                except Exception as e:  # pylint: disable=broad-except
                    error_message = traceback.format_exc()
                    logger.error(f"Unexpected error during upload: {type(e).__name__}")
                    logger.error(f"Error message: {str(e)}")
                    logger.error(f"Full traceback:\\n{error_message}")
                    yield None

            logger.info("=== BUILDKITE TEST COLLECTOR UPLOAD DEBUG END ===")'''

    if old_submit in content:
        content = content.replace(old_submit, new_submit)
        with open(api_file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"✓ Patched API file: {api_file_path}")
    else:
        print(
            f"⚠ Could not find exact match in API file, attempting flexible patch..."
        )
        # Try a more flexible approach - find the method and replace it
        pattern = r'(def submit\(self, payload: Payload, batch_size=100\).*?)(\n    def |\nclass |\Z)'
        match = re.search(pattern, content, re.DOTALL)
        if match:
            # This is more complex, let's just append our logging at the start
            # For now, let's use a simpler approach - replace the whole file
            print(f"⚠ Using backup patching method for API file")
            # Actually, let's write the full patched version
            patched_content = content.replace(
                'if not self.ci:\n            yield None',
                'if not self.ci:\n            logger.warning("CI environment variable is not set, skipping upload")\n            logger.info("=== BUILDKITE TEST COLLECTOR UPLOAD DEBUG END (CI not set) ===")\n            yield None\n            return'
            ).replace(
                'if not self.token:\n            logger.warning("No %s environment variable present", self.ENV_TOKEN)\n            yield None',
                'if not self.token:\n            logger.warning("No %s environment variable present", self.ENV_TOKEN)\n            logger.info("=== BUILDKITE TEST COLLECTOR UPLOAD DEBUG END (no token) ===")\n            yield None\n            return'
            )
            # Add logging at the start
            if 'logger.info("=== BUILDKITE TEST COLLECTOR UPLOAD DEBUG START ===")' not in patched_content:
                patched_content = patched_content.replace(
                    'def submit(self, payload: Payload, batch_size=100) -> Generator[Optional[Response], Any, Any]:\n        """Submit a payload to the API"""\n        response = None',
                    '''def submit(self, payload: Payload, batch_size=100) -> Generator[Optional[Response], Any, Any]:
        """Submit a payload to the API"""
        response = None

        logger.info("=== BUILDKITE TEST COLLECTOR UPLOAD DEBUG START ===")
        logger.info(f"CI environment variable: {self.ci}")
        logger.info(f"BUILDKITE_ANALYTICS_TOKEN present: {bool(self.token)}")
        logger.info(f"BUILDKITE_ANALYTICS_TOKEN length: {len(self.token) if self.token else 0}")
        logger.info(f"API URL: {self.api_url}")

        payload_json = payload.as_json()
        payload_data = payload_json.get("data", [])
        logger.info(f"Payload contains {len(payload_data)} test(s)")
        logger.info(f"Payload JSON size: {len(str(payload_json))} characters")'''
                )
            # Add detailed logging in the upload loop
            if 'logger.info(f"=== Uploading batch' not in patched_content:
                patched_content = patched_content.replace(
                    'for payload_slice in payload.into_batches(batch_size):',
                    '''batches = list(payload.into_batches(batch_size))
            logger.info(f"Payload will be split into {len(batches)} batch(es)")

            for batch_idx, payload_slice in enumerate(batches):'''
                ).replace(
                    'response = post(self.api_url + "/uploads",',
                    '''upload_url = self.api_url + "/uploads"
                    payload_json_data = payload_slice.as_json()
                    payload_size = len(str(payload_json_data))
                    num_tests = len(payload_json_data.get("data", []))

                    logger.info(f"=== Uploading batch {batch_idx + 1}/{len(batches)} ===")
                    logger.info(f"Upload URL: {upload_url}")
                    logger.info(f"Batch payload size: {payload_size} characters")
                    logger.info(f"Batch contains {num_tests} test(s)")
                    logger.info(f"Authorization header: Token token=\\"***\\" (token length: {len(self.token)})")

                    response = post(upload_url,'''
                ).replace(
                    'json=payload_slice.as_json(),', 'json=payload_json_data,'
                ).replace(
                    'response.raise_for_status()\n                    yield response',
                    '''response.raise_for_status()

                    logger.info(f"HTTP Status Code: {response.status_code}")
                    logger.info(f"Response Headers: {dict(response.headers)}")
                    logger.info(f"Response Body: {response.text[:500]}")  # First 500 chars

                    logger.info(f"✓ Successfully uploaded batch {batch_idx + 1}/{len(batches)}")
                    yield response'''
                ).replace(
                    'except HTTPError as err:\n                    logger.warning("Failed to uploads test results to buildkite")\n                    logger.warning(err)\n                    yield None',
                    '''except HTTPError as err:
                    logger.error("Failed to upload test results to buildkite")
                    logger.error(f"HTTP Error: {err}")
                    logger.error(f"Response status: {err.response.status_code if err.response else 'N/A'}")
                    logger.error(f"Response text: {err.response.text if err.response else 'N/A'}")
                    logger.error(f"Response headers: {dict(err.response.headers) if err.response else 'N/A'}")
                    yield None'''
                ).replace(
                    'except Exception:  # pylint: disable=broad-except\n                    error_message = traceback.format_exc()\n                    logger.warning(error_message)\n                    yield None',
                    '''except Exception as e:  # pylint: disable=broad-except
                    error_message = traceback.format_exc()
                    logger.error(f"Unexpected error during upload: {type(e).__name__}")
                    logger.error(f"Error message: {str(e)}")
                    logger.error(f"Full traceback:\\n{error_message}")
                    yield None

            logger.info("=== BUILDKITE TEST COLLECTOR UPLOAD DEBUG END ===")''')

            with open(api_file_path, 'w', encoding='utf-8') as f:
                f.write(patched_content)
            print(f"✓ Patched API file (flexible method): {api_file_path}")


def patch_plugin_init_file(init_file_path):
    """Patch the pytest plugin __init__.py to add detailed logging."""
    with open(init_file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Check if already patched
    if '=== pytest_unconfigure hook called ===' in content:
        print(f"Plugin init file already patched: {init_file_path}")
        return

    # Add import at the top if not present
    if 'from .logger import logger' not in content:
        # Find the last import statement
        import_pattern = r'(from \.buildkite_plugin import BuildkitePlugin\n)'
        content = re.sub(import_pattern, r'\1from .logger import logger\n',
                         content)

    # Patch pytest_unconfigure function
    old_unconfigure = '''@pytest.hookimpl
def pytest_unconfigure(config):
    """pytest_unconfigure hook callback"""
    plugin = getattr(config, '_buildkite', None)

    if plugin:
        api = API(os.environ)
        xdist_enabled = (
            config.pluginmanager.getplugin("xdist") is not None
            and config.getoption("numprocesses") is not None
        )
        is_xdist_worker = hasattr(config, 'workerinput')

        is_controller = not xdist_enabled or (xdist_enabled and not is_xdist_worker)

        # When xdist is not installed, or when it's installed and not enabled
        if not xdist_enabled:
            list(api.submit(plugin.payload))

        # When xdist is activated, we want to submit from worker thread only, because they have
        # access to tag data
        if xdist_enabled and is_xdist_worker:
            list(api.submit(plugin.payload))

        # We only want a single thread to write to the json file.
        # When xdist is enabled, that will be the controller thread.
        if is_controller:
            # Note that when xdist is used, this JSON output file will NOT contain tags.
            jsonpath = config.option.jsonpath
            if jsonpath:
                plugin.save_payload_as_json(jsonpath, merge=config.option.mergejson)

        del config._buildkite
        config.pluginmanager.unregister(plugin)'''

    new_unconfigure = '''@pytest.hookimpl
def pytest_unconfigure(config):
    """pytest_unconfigure hook callback"""
    logger.info("=== pytest_unconfigure hook called ===")
    plugin = getattr(config, '_buildkite', None)

    if plugin:
        logger.info("Buildkite plugin found, initializing API client")
        api = API(os.environ)
        xdist_enabled = (
            config.pluginmanager.getplugin("xdist") is not None
            and config.getoption("numprocesses") is not None
        )
        is_xdist_worker = hasattr(config, 'workerinput')

        is_controller = not xdist_enabled or (xdist_enabled and not is_xdist_worker)

        logger.info(f"xdist_enabled: {xdist_enabled}")
        logger.info(f"is_xdist_worker: {is_xdist_worker}")
        logger.info(f"is_controller: {is_controller}")

        payload_json = plugin.payload.as_json()
        num_tests = len(payload_json.get("data", []))
        logger.info(f"Payload contains {num_tests} test(s)")

        # When xdist is not installed, or when it's installed and not enabled
        if not xdist_enabled:
            logger.info("xdist not enabled, submitting from main thread")
            responses = list(api.submit(plugin.payload))
            logger.info(f"Upload completed, received {len(responses)} response(s)")
            for idx, resp in enumerate(responses):
                if resp:
                    logger.info(f"Response {idx + 1}: Status {resp.status_code}")
                else:
                    logger.warning(f"Response {idx + 1}: None (upload may have been skipped)")

        # When xdist is activated, we want to submit from worker thread only, because they have
        # access to tag data
        if xdist_enabled and is_xdist_worker:
            logger.info("xdist enabled and is worker thread, submitting from worker")
            responses = list(api.submit(plugin.payload))
            logger.info(f"Upload completed, received {len(responses)} response(s)")
            for idx, resp in enumerate(responses):
                if resp:
                    logger.info(f"Response {idx + 1}: Status {resp.status_code}")
                else:
                    logger.warning(f"Response {idx + 1}: None (upload may have been skipped)")

        # We only want a single thread to write to the json file.
        # When xdist is enabled, that will be the controller thread.
        if is_controller:
            # Note that when xdist is used, this JSON output file will NOT contain tags.
            jsonpath = config.option.jsonpath
            if jsonpath:
                logger.info(f"Saving payload to JSON file: {jsonpath}")
                plugin.save_payload_as_json(jsonpath, merge=config.option.mergejson)
            else:
                logger.info("No JSON path specified, skipping JSON file save")

        del config._buildkite
        config.pluginmanager.unregister(plugin)
        logger.info("Buildkite plugin unregistered")
    else:
        logger.warning("Buildkite plugin not found in config")

    logger.info("=== pytest_unconfigure hook completed ===")'''

    if old_unconfigure in content:
        content = content.replace(old_unconfigure, new_unconfigure)
        with open(init_file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"✓ Patched plugin init file: {init_file_path}")
    else:
        # Try flexible patching
        # Add logger import if needed
        if 'from .logger import logger' not in content:
            content = content.replace(
                'from .buildkite_plugin import BuildkitePlugin',
                'from .buildkite_plugin import BuildkitePlugin\nfrom .logger import logger'
            )

        # Add logging to pytest_unconfigure
        content = re.sub(
            r'(def pytest_unconfigure\(config\):)\n    """pytest_unconfigure hook callback"""\n    plugin =',
            r'\1\n    """pytest_unconfigure hook callback"""\n    logger.info("=== pytest_unconfigure hook called ===")\n    plugin =',
            content)

        # Add logging after plugin check
        content = content.replace(
            'if plugin:\n        api = API(os.environ)', '''if plugin:
        logger.info("Buildkite plugin found, initializing API client")
        api = API(os.environ)''')

        # Add xdist logging
        content = content.replace(
            'is_controller = not xdist_enabled or (xdist_enabled and not is_xdist_worker)',
            '''is_controller = not xdist_enabled or (xdist_enabled and not is_xdist_worker)

        logger.info(f"xdist_enabled: {xdist_enabled}")
        logger.info(f"is_xdist_worker: {is_xdist_worker}")
        logger.info(f"is_controller: {is_controller}")

        payload_json = plugin.payload.as_json()
        num_tests = len(payload_json.get("data", []))
        logger.info(f"Payload contains {num_tests} test(s)")''')

        # Add logging for upload calls
        content = content.replace(
            'if not xdist_enabled:\n            list(api.submit(plugin.payload))',
            '''if not xdist_enabled:
            logger.info("xdist not enabled, submitting from main thread")
            responses = list(api.submit(plugin.payload))
            logger.info(f"Upload completed, received {len(responses)} response(s)")
            for idx, resp in enumerate(responses):
                if resp:
                    logger.info(f"Response {idx + 1}: Status {resp.status_code}")
                else:
                    logger.warning(f"Response {idx + 1}: None (upload may have been skipped)")'''
        )

        content = content.replace(
            'if xdist_enabled and is_xdist_worker:\n            list(api.submit(plugin.payload))',
            '''if xdist_enabled and is_xdist_worker:
            logger.info("xdist enabled and is worker thread, submitting from worker")
            responses = list(api.submit(plugin.payload))
            logger.info(f"Upload completed, received {len(responses)} response(s)")
            for idx, resp in enumerate(responses):
                if resp:
                    logger.info(f"Response {idx + 1}: Status {resp.status_code}")
                else:
                    logger.warning(f"Response {idx + 1}: None (upload may have been skipped)")'''
        )

        # Add logging for JSON save
        content = content.replace(
            'if jsonpath:\n                plugin.save_payload_as_json(jsonpath, merge=config.option.mergejson)',
            '''if jsonpath:
                logger.info(f"Saving payload to JSON file: {jsonpath}")
                plugin.save_payload_as_json(jsonpath, merge=config.option.mergejson)
            else:
                logger.info("No JSON path specified, skipping JSON file save")'''
        )

        # Add final logging
        content = content.replace(
            'del config._buildkite\n        config.pluginmanager.unregister(plugin)',
            '''del config._buildkite
        config.pluginmanager.unregister(plugin)
        logger.info("Buildkite plugin unregistered")
    else:
        logger.warning("Buildkite plugin not found in config")

    logger.info("=== pytest_unconfigure hook completed ===")''')

        with open(init_file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"✓ Patched plugin init file (flexible method): {init_file_path}")


def main():
    """Main patching function."""
    print(
        "Patching buildkite-test-collector package for detailed upload logging..."
    )

    try:
        package_path = find_buildkite_collector_package()
        print(f"Found buildkite-test-collector at: {package_path}")

        api_file = os.path.join(package_path, 'collector', 'api.py')
        init_file = os.path.join(package_path, 'pytest_plugin', '__init__.py')

        if not os.path.exists(api_file):
            raise FileNotFoundError(f"API file not found: {api_file}")
        if not os.path.exists(init_file):
            raise FileNotFoundError(f"Plugin init file not found: {init_file}")

        patch_api_file(api_file)
        patch_plugin_init_file(init_file)

        print("✓ Successfully patched buildkite-test-collector package")
        return 0

    except Exception as e:
        print(f"✗ Error patching buildkite-test-collector: {e}",
              file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
