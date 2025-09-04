"""Unit tests for ARM64 mounting utilities in sky.data.mounting_utils."""

import unittest
from unittest import mock

from sky.data import mounting_utils


class TestMountingUtilsArm64(unittest.TestCase):
    """Test ARM64-specific functionality in mounting utilities."""

    def setUp(self):
        """Set up test fixtures."""
        self.bucket_name = 'test-bucket'
        self.mount_path = '/mnt/test'
        self.bucket_sub_path = 'sub/path'

    @mock.patch('platform.machine')
    def test_s3_mount_install_cmd_arm64(self, mock_machine):
        """Test S3 mount install command for ARM64 architecture."""
        mock_machine.return_value = 'aarch64'

        cmd = mounting_utils.get_s3_mount_install_cmd()

        # Should contain architecture detection
        self.assertIn('ARCH=$(uname -m)', cmd)
        # Should contain ARM64 conditional logic
        self.assertIn('if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]',
                      cmd)
        # Should install rclone for ARM64
        self.assertIn('rclone', cmd)
        self.assertIn('ARCH_SUFFIX="arm64"', cmd)
        # Should contain fallback for x86_64 (goofys)
        self.assertIn('goofys', cmd)

    @mock.patch('platform.machine')
    def test_s3_mount_install_cmd_x86_64(self, mock_machine):
        """Test S3 mount install command for x86_64 architecture."""
        mock_machine.return_value = 'x86_64'

        cmd = mounting_utils.get_s3_mount_install_cmd()

        # Should contain architecture detection
        self.assertIn('ARCH=$(uname -m)', cmd)
        # Should still contain ARM64 conditional for runtime detection
        self.assertIn('if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]',
                      cmd)
        # Should contain goofys installation in else branch
        self.assertIn('goofys', cmd)

    def test_s3_mount_cmd_structure(self):
        """Test S3 mount command structure for ARM64 support."""
        cmd = mounting_utils.get_s3_mount_cmd(self.bucket_name, self.mount_path)

        # Should contain architecture detection
        self.assertIn('ARCH=$(uname -m)', cmd)
        # Should contain ARM64 conditional
        self.assertIn('if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]',
                      cmd)
        # Should contain rclone mount command for ARM64
        self.assertIn('rclone mount :s3:', cmd)
        self.assertIn('--daemon --allow-other', cmd)
        # Should contain goofys command for x86_64
        self.assertIn('goofys', cmd)
        self.assertIn('--stat-cache-ttl', cmd)
        self.assertIn('--type-cache-ttl', cmd)

    def test_s3_mount_cmd_with_sub_path(self):
        """Test S3 mount command with bucket sub path."""
        cmd = mounting_utils.get_s3_mount_cmd(self.bucket_name, self.mount_path,
                                              self.bucket_sub_path)

        # Should include sub path in both rclone and goofys commands
        expected_sub_path = f':{self.bucket_sub_path}'
        self.assertIn(f':s3:{self.bucket_name}{expected_sub_path}', cmd)
        self.assertIn(f'{self.bucket_name}{expected_sub_path}', cmd)

    def test_s3_mount_cmd_without_sub_path(self):
        """Test S3 mount command without bucket sub path."""
        cmd = mounting_utils.get_s3_mount_cmd(self.bucket_name, self.mount_path)

        # Should not include colon prefix when no sub path
        self.assertIn(f':s3:{self.bucket_name}', cmd)
        self.assertIn(f'{self.bucket_name} {self.mount_path}', cmd)

    def test_nebius_mount_cmd_structure(self):
        """Test Nebius mount command structure for ARM64 support."""
        profile_name = 'test-profile'
        endpoint_url = 'https://storage.test.com'

        cmd = mounting_utils.get_nebius_mount_cmd(profile_name,
                                                  self.bucket_name,
                                                  endpoint_url, self.mount_path)

        # Should contain architecture detection
        self.assertIn('ARCH=$(uname -m)', cmd)
        # Should contain ARM64 conditional
        self.assertIn('if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]',
                      cmd)
        # Should contain rclone mount with AWS profile and endpoint
        self.assertIn(f'AWS_PROFILE={profile_name}', cmd)
        self.assertIn(f'--s3-endpoint {endpoint_url}', cmd)
        self.assertIn('rclone mount :s3:', cmd)
        # Should contain goofys command with AWS profile and endpoint
        self.assertIn('goofys', cmd)
        self.assertIn(f'--endpoint {endpoint_url}', cmd)

    def test_nebius_mount_cmd_with_sub_path(self):
        """Test Nebius mount command with bucket sub path."""
        profile_name = 'test-profile'
        endpoint_url = 'https://storage.test.com'

        cmd = mounting_utils.get_nebius_mount_cmd(profile_name,
                                                  self.bucket_name,
                                                  endpoint_url, self.mount_path,
                                                  self.bucket_sub_path)

        # Should include sub path in both rclone and goofys commands
        expected_sub_path = f':{self.bucket_sub_path}'
        self.assertIn(f':s3:{self.bucket_name}{expected_sub_path}', cmd)
        self.assertIn(f'{self.bucket_name}{expected_sub_path}', cmd)

    def test_r2_mount_cmd_structure(self):
        """Test R2 mount command structure for ARM64 support."""
        credentials_path = '/path/to/credentials'
        profile_name = 'test-profile'
        endpoint_url = 'https://r2.test.com'

        cmd = mounting_utils.get_r2_mount_cmd(credentials_path, profile_name,
                                              endpoint_url, self.bucket_name,
                                              self.mount_path)

        # Should contain architecture detection
        self.assertIn('ARCH=$(uname -m)', cmd)
        # Should contain ARM64 conditional
        self.assertIn('if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]',
                      cmd)
        # Should contain rclone mount with credentials and endpoint
        self.assertIn(f'AWS_SHARED_CREDENTIALS_FILE={credentials_path}', cmd)
        self.assertIn(f'AWS_PROFILE={profile_name}', cmd)
        self.assertIn(f'--s3-endpoint {endpoint_url}', cmd)
        self.assertIn('rclone mount :s3:', cmd)
        # Should contain goofys command with credentials and endpoint
        self.assertIn('goofys', cmd)
        self.assertIn(f'--endpoint {endpoint_url}', cmd)

    def test_r2_mount_cmd_with_sub_path(self):
        """Test R2 mount command with bucket sub path."""
        credentials_path = '/path/to/credentials'
        profile_name = 'test-profile'
        endpoint_url = 'https://r2.test.com'

        cmd = mounting_utils.get_r2_mount_cmd(credentials_path, profile_name,
                                              endpoint_url, self.bucket_name,
                                              self.mount_path,
                                              self.bucket_sub_path)

        # Should include sub path in both rclone and goofys commands
        expected_sub_path = f':{self.bucket_sub_path}'
        self.assertIn(f':s3:{self.bucket_name}{expected_sub_path}', cmd)
        self.assertIn(f'{self.bucket_name}{expected_sub_path}', cmd)

    def test_mount_binary_detection_rclone(self):
        """Test mount binary detection for rclone commands."""
        # Test with rclone command
        rclone_cmd = 'rclone mount :s3:bucket /mnt/test --daemon'
        binary = mounting_utils._get_mount_binary(rclone_cmd)
        self.assertEqual(binary, 'rclone')

    def test_mount_binary_detection_goofys(self):
        """Test mount binary detection for goofys commands."""
        # Test with goofys command
        goofys_cmd = 'goofys -o allow_other bucket /mnt/test'
        binary = mounting_utils._get_mount_binary(goofys_cmd)
        self.assertEqual(binary, 'goofys')

    def test_fusermount3_soft_link_in_rclone_commands(self):
        """Test that rclone commands include fusermount3 soft link setup."""
        # Test S3 mount command
        s3_cmd = mounting_utils.get_s3_mount_cmd(self.bucket_name,
                                                 self.mount_path)
        self.assertIn(mounting_utils.FUSERMOUNT3_SOFT_LINK_CMD, s3_cmd)

        # Test Nebius mount command
        nebius_cmd = mounting_utils.get_nebius_mount_cmd(
            'profile', self.bucket_name, 'https://endpoint.com',
            self.mount_path)
        self.assertIn(mounting_utils.FUSERMOUNT3_SOFT_LINK_CMD, nebius_cmd)

        # Test R2 mount command
        r2_cmd = mounting_utils.get_r2_mount_cmd('/creds', 'profile',
                                                 'https://endpoint.com',
                                                 self.bucket_name,
                                                 self.mount_path)
        self.assertIn(mounting_utils.FUSERMOUNT3_SOFT_LINK_CMD, r2_cmd)

    def test_rclone_version_consistency(self):
        """Test that all mount functions use consistent rclone version."""
        # Test S3 install command uses RCLONE_VERSION
        s3_install = mounting_utils.get_s3_mount_install_cmd()
        self.assertIn(mounting_utils.RCLONE_VERSION, s3_install)

        # Test general rclone install command
        rclone_install = mounting_utils.get_rclone_install_cmd()
        self.assertIn(mounting_utils.RCLONE_VERSION, rclone_install)

    def test_mount_script_generation_with_rclone(self):
        """Test mount script generation includes proper rclone binary detection."""
        mount_cmd = 'rclone mount :s3:bucket /mnt/test --daemon'
        install_cmd = 'echo "install rclone"'

        script = mounting_utils.get_mounting_script(self.mount_path, mount_cmd,
                                                    install_cmd)

        # Should set MOUNT_BINARY to rclone
        self.assertIn('MOUNT_BINARY=rclone', script)
        # Should check for rclone installation
        self.assertIn('[ -x "$(command -v rclone)" ]', script)

    def test_mount_script_generation_with_goofys(self):
        """Test mount script generation includes proper goofys binary detection."""
        mount_cmd = 'goofys -o allow_other bucket /mnt/test'
        install_cmd = 'echo "install goofys"'

        script = mounting_utils.get_mounting_script(self.mount_path, mount_cmd,
                                                    install_cmd)

        # Should set MOUNT_BINARY to goofys
        self.assertIn('MOUNT_BINARY=goofys', script)
        # Should check for goofys installation
        self.assertIn('[ -x "$(command -v goofys)" ]', script)

    def test_architecture_specific_package_selection(self):
        """Test that ARM and x86_64 use different package suffixes."""
        install_cmd = mounting_utils.get_s3_mount_install_cmd()

        # ARM64 should use "arm64" suffix for rclone
        self.assertIn('ARCH_SUFFIX="arm64"', install_cmd)
        # Should have dpkg installation for ARM with arm suffix
        self.assertIn(
            f'rclone-{mounting_utils.RCLONE_VERSION}-linux-${{ARCH_SUFFIX}}.deb',
            install_cmd)
        # Should have yum installation for ARM with arm64 suffix
        self.assertIn(
            f'rclone-{mounting_utils.RCLONE_VERSION}-linux-${{ARCH_SUFFIX}}.rpm',
            install_cmd)


if __name__ == '__main__':
    unittest.main()
