"""Unit tests for Helm chart config override warning functionality."""

import subprocess
import tempfile
import os
import unittest


class TestHelmConfigWarning(unittest.TestCase):
    """Test cases for the config override warning in Helm chart."""

    def setUp(self):
        """Set up test environment."""
        self.chart_path = 'charts/skypilot'
        # Check if chart directory exists
        if not os.path.exists(self.chart_path):
            self.skipTest("Chart directory not found")
        
        # Check if helm is available
        try:
            subprocess.run(['helm', 'version'], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            self.skipTest("Helm not available")

    def _run_helm_template(self, values_content, release_name="test-release"):
        """Run helm template with given values and return the output."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', 
                                         delete=False) as f:
            f.write(values_content)
            f.flush()
            
            try:
                cmd = [
                    'helm', 'template', release_name, self.chart_path,
                    '-f', f.name
                ]
                result = subprocess.run(cmd, capture_output=True, text=True)
                return result.returncode, result.stdout, result.stderr
            finally:
                os.unlink(f.name)

    def test_no_config_no_warning(self):
        """Test that no warning is shown when config is not set."""
        values = """
apiService:
  config: null
"""
        returncode, stdout, stderr = self._run_helm_template(values)
        self.assertEqual(returncode, 0, 
                         f"Expected success but got error: {stderr}")
        self.assertNotIn("WARNING: Config Override Detected", stderr)

    def test_config_without_confirmation_shows_warning(self):
        """Test that warning is shown when config is set without confirmation."""
        values = """
apiService:
  config: |
    test_config: value
  confirmConfigOverride: false
"""
        returncode, stdout, stderr = self._run_helm_template(values)
        self.assertNotEqual(returncode, 0, 
                            "Expected failure due to warning")
        self.assertIn("WARNING: Config Override Detected", stderr)
        self.assertIn("confirmConfigOverride=true", stderr)

    def test_config_with_confirmation_no_warning(self):
        """Test that no warning is shown when config is set with confirmation."""
        values = """
apiService:
  config: |
    test_config: value
  confirmConfigOverride: true
"""
        returncode, stdout, stderr = self._run_helm_template(values)
        self.assertEqual(returncode, 0, 
                         f"Expected success but got error: {stderr}")
        self.assertNotIn("WARNING: Config Override Detected", stderr)

    def test_empty_config_no_warning(self):
        """Test that no warning is shown when config is empty string."""
        values = """
apiService:
  config: ""
"""
        returncode, stdout, stderr = self._run_helm_template(values)
        self.assertEqual(returncode, 0, 
                         f"Expected success but got error: {stderr}")
        self.assertNotIn("WARNING: Config Override Detected", stderr)

    def test_release_name_in_configmap_reference(self):
        """Test that the actual release name appears in ConfigMap references."""
        values = """
apiService:
  config: |
    test_config: value
  confirmConfigOverride: true
"""
        release_name = "my-custom-release"
        returncode, stdout, stderr = self._run_helm_template(values, release_name)
        self.assertEqual(returncode, 0, 
                         f"Expected success but got error: {stderr}")
        # Check that the ConfigMap name includes the actual release name
        self.assertIn(f"{release_name}-config", stdout)

    def test_namespace_in_configmap_reference(self):
        """Test that templates work correctly with custom namespaces."""
        values = """
apiService:
  config: |
    test_config: value
  confirmConfigOverride: true
"""
        release_name = "my-custom-release"
        # Test with helm template using --namespace flag
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', 
                                         delete=False) as f:
            f.write(values)
            f.flush()
            
            try:
                cmd = [
                    'helm', 'template', release_name, self.chart_path,
                    '-f', f.name, '--namespace', 'my-namespace'
                ]
                result = subprocess.run(cmd, capture_output=True, text=True)
                returncode, stdout, stderr = result.returncode, result.stdout, result.stderr
            finally:
                os.unlink(f.name)
        
        self.assertEqual(returncode, 0, 
                         f"Expected success but got error: {stderr}")
        # Check that the ConfigMap name includes the actual release name
        self.assertIn(f"{release_name}-config", stdout)
        # Check that namespace is referenced in the templates
        self.assertIn("my-namespace", stdout)


if __name__ == '__main__':
    unittest.main() 
