"""Unit tests for mount command default options in sky.data.mounting_utils.

These tests verify that all mount command functions produce commands with
the expected default options. They serve as a baseline to detect unintended
changes when modifying mount logic.
"""

import unittest

from sky.data import mounting_utils


class TestS3MountCmdDefaults(unittest.TestCase):
    """Test default options for S3 mount commands (goofys + rclone)."""

    def setUp(self):
        self.bucket_name = 'test-bucket'
        self.mount_path = '/mnt/test'

    def test_goofys_default_options(self):
        """Verify goofys branch includes all default flags."""
        cmd = mounting_utils.get_s3_mount_cmd(self.bucket_name, self.mount_path)
        # goofys default options with exact values
        self.assertIn('-o allow_other', cmd)
        self.assertIn(
            f'--stat-cache-ttl {mounting_utils._STAT_CACHE_TTL}',  # pylint: disable=protected-access
            cmd)
        self.assertIn(
            f'--type-cache-ttl {mounting_utils._TYPE_CACHE_TTL}',  # pylint: disable=protected-access
            cmd)
        # Verify exact default values
        self.assertIn('--stat-cache-ttl 5s', cmd)
        self.assertIn('--type-cache-ttl 5s', cmd)

    def test_rclone_default_options(self):
        """Verify rclone branch includes all default flags."""
        cmd = mounting_utils.get_s3_mount_cmd(self.bucket_name, self.mount_path)
        self.assertIn('--daemon', cmd)
        self.assertIn('--allow-other', cmd)
        self.assertIn('--s3-env-auth=true', cmd)
        # Full rclone flag string
        self.assertIn('--daemon --allow-other --s3-env-auth=true', cmd)

    def test_architecture_branching(self):
        """Verify command has both ARM64 and x86 branches."""
        cmd = mounting_utils.get_s3_mount_cmd(self.bucket_name, self.mount_path)
        self.assertIn('ARCH=$(uname -m)', cmd)
        self.assertIn('if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]',
                      cmd)
        self.assertIn('rclone mount :s3:', cmd)
        self.assertIn('goofys', cmd)

    def test_bucket_and_mount_path(self):
        """Verify bucket name and mount path are in both branches."""
        cmd = mounting_utils.get_s3_mount_cmd(self.bucket_name, self.mount_path)
        # rclone branch
        self.assertIn(f':s3:{self.bucket_name} {self.mount_path}', cmd)
        # goofys branch
        self.assertIn(f'{self.bucket_name} {self.mount_path}', cmd)

    def test_sub_path(self):
        """Verify sub path is formatted correctly in both branches."""
        cmd = mounting_utils.get_s3_mount_cmd(self.bucket_name, self.mount_path,
                                              'data/sub')
        self.assertIn(f':s3:{self.bucket_name}:data/sub', cmd)
        self.assertIn(f'{self.bucket_name}:data/sub', cmd)

    def test_no_sub_path(self):
        """Verify no colon suffix when sub path is None."""
        cmd = mounting_utils.get_s3_mount_cmd(self.bucket_name, self.mount_path,
                                              None)
        # Should have bucket name followed by space, not colon
        self.assertIn(f':s3:{self.bucket_name} ', cmd)

    def test_fusermount3_setup(self):
        """Verify fusermount3 setup commands in rclone branch."""
        cmd = mounting_utils.get_s3_mount_cmd(self.bucket_name, self.mount_path)
        self.assertIn(mounting_utils.FUSE3_INSTALL_CMD, cmd)
        self.assertIn(mounting_utils.FUSERMOUNT3_SOFT_LINK_CMD, cmd)


class TestGcsMountCmdDefaults(unittest.TestCase):
    """Test default options for GCS mount commands (gcsfuse)."""

    def setUp(self):
        self.bucket_name = 'test-bucket'
        self.mount_path = '/mnt/test'

    def test_gcsfuse_default_options(self):
        """Verify gcsfuse includes all default flags with exact values."""
        cmd = mounting_utils.get_gcs_mount_cmd(self.bucket_name,
                                               self.mount_path)
        self.assertIn('--debug_fuse_errors', cmd)
        self.assertIn('-o allow_other', cmd)
        self.assertIn('--implicit-dirs', cmd)
        self.assertIn(
            f'--stat-cache-capacity {mounting_utils._STAT_CACHE_CAPACITY}',  # pylint: disable=protected-access
            cmd)
        self.assertIn(
            f'--stat-cache-ttl {mounting_utils._STAT_CACHE_TTL}',  # pylint: disable=protected-access
            cmd)
        self.assertIn(
            f'--type-cache-ttl {mounting_utils._TYPE_CACHE_TTL}',  # pylint: disable=protected-access
            cmd)
        self.assertIn(
            f'--rename-dir-limit {mounting_utils._RENAME_DIR_LIMIT}',  # pylint: disable=protected-access
            cmd)
        # Exact values
        self.assertIn('--stat-cache-capacity 4096', cmd)
        self.assertIn('--stat-cache-ttl 5s', cmd)
        self.assertIn('--type-cache-ttl 5s', cmd)
        self.assertIn('--rename-dir-limit 10000', cmd)

    def test_log_file(self):
        """Verify gcsfuse has log file configured."""
        cmd = mounting_utils.get_gcs_mount_cmd(self.bucket_name,
                                               self.mount_path)
        self.assertIn('--log-file', cmd)
        self.assertIn('gcsfuse.XXXX.log', cmd)

    def test_bucket_and_mount_path(self):
        """Verify bucket name and mount path are in command."""
        cmd = mounting_utils.get_gcs_mount_cmd(self.bucket_name,
                                               self.mount_path)
        self.assertIn(f'{self.bucket_name} {self.mount_path}', cmd)

    def test_sub_path(self):
        """Verify --only-dir is used for sub path."""
        cmd = mounting_utils.get_gcs_mount_cmd(self.bucket_name,
                                               self.mount_path, 'data/sub')
        self.assertIn('--only-dir data/sub', cmd)

    def test_no_sub_path(self):
        """Verify no --only-dir when sub path is None."""
        cmd = mounting_utils.get_gcs_mount_cmd(self.bucket_name,
                                               self.mount_path, None)
        self.assertNotIn('--only-dir', cmd)

    def test_command_starts_with_gcsfuse(self):
        """Verify the command binary is gcsfuse."""
        cmd = mounting_utils.get_gcs_mount_cmd(self.bucket_name,
                                               self.mount_path)
        self.assertTrue(cmd.startswith('gcsfuse'))


class TestAzMountCmdDefaults(unittest.TestCase):
    """Test default options for Azure blobfuse2 mount commands."""

    def setUp(self):
        self.container_name = 'test-container'
        self.storage_account = 'teststorage'
        self.mount_path = '/mnt/test'
        self.storage_key = 'dGVzdGtleQ=='

    def test_blobfuse2_default_options(self):
        """Verify blobfuse2 includes all default flags."""
        cmd = mounting_utils.get_az_mount_cmd(self.container_name,
                                              self.storage_account,
                                              self.mount_path, self.storage_key)
        self.assertIn('--no-symlinks', cmd)
        self.assertIn('--tmp-path', cmd)
        self.assertIn(f'--container-name {self.container_name}', cmd)

    def test_tmp_path_uses_cache_dir(self):
        """Verify --tmp-path uses the blobfuse cache dir with boot time."""
        cmd = mounting_utils.get_az_mount_cmd(self.container_name,
                                              self.storage_account,
                                              self.mount_path, self.storage_key)
        expected_cache = mounting_utils._BLOBFUSE_CACHE_DIR.format(  # pylint: disable=protected-access
            storage_account_name=self.storage_account,
            container_name=self.container_name)
        self.assertIn(f'--tmp-path {expected_cache}_', cmd)
        # Verify boot time command is used for unique cache dir
        self.assertIn('date +%s -d "$(uptime -s)"', cmd)

    def test_fusermount_wrapper_options(self):
        """Verify fusermount-wrapper path includes correct options."""
        cmd = mounting_utils.get_az_mount_cmd(self.container_name,
                                              self.storage_account,
                                              self.mount_path, self.storage_key)
        # fusermount-wrapper path
        self.assertIn('fusermount-wrapper', cmd)
        self.assertIn('-o nonempty', cmd)
        self.assertIn('--foreground', cmd)
        self.assertIn(f'-m {self.mount_path}', cmd)

    def test_fuse_mount_options(self):
        """Verify FUSE mount options (allow_other, default_permissions)."""
        cmd = mounting_utils.get_az_mount_cmd(self.container_name,
                                              self.storage_account,
                                              self.mount_path, self.storage_key)
        # fusermount-wrapper uses comma-separated -o
        self.assertIn('-o allow_other,default_permissions', cmd)
        # original path uses separate -o flags
        self.assertIn('-o allow_other', cmd)
        self.assertIn('-o default_permissions', cmd)

    def test_fallback_to_original_mount(self):
        """Verify fallback command when fusermount-wrapper not available."""
        cmd = mounting_utils.get_az_mount_cmd(self.container_name,
                                              self.storage_account,
                                              self.mount_path, self.storage_key)
        # command -v check for fusermount-wrapper
        self.assertIn('command -v fusermount-wrapper', cmd)
        # original blobfuse2 command (without fusermount-wrapper)
        self.assertIn('blobfuse2 --no-symlinks', cmd)

    def test_storage_account_env_var(self):
        """Verify AZURE_STORAGE_ACCOUNT is set."""
        cmd = mounting_utils.get_az_mount_cmd(self.container_name,
                                              self.storage_account,
                                              self.mount_path, self.storage_key)
        self.assertIn(f'AZURE_STORAGE_ACCOUNT={self.storage_account}', cmd)

    def test_private_container_key(self):
        """Verify AZURE_STORAGE_ACCESS_KEY is set for private containers."""
        cmd = mounting_utils.get_az_mount_cmd(self.container_name,
                                              self.storage_account,
                                              self.mount_path, self.storage_key)
        self.assertIn('AZURE_STORAGE_ACCESS_KEY=', cmd)

    def test_public_container_sas_token(self):
        """Verify AZURE_STORAGE_SAS_TOKEN is set for public containers."""
        cmd = mounting_utils.get_az_mount_cmd(self.container_name,
                                              self.storage_account,
                                              self.mount_path,
                                              storage_account_key=None)
        self.assertIn('AZURE_STORAGE_SAS_TOKEN=', cmd)
        self.assertNotIn('AZURE_STORAGE_ACCESS_KEY=', cmd)

    def test_sub_path(self):
        """Verify --subdirectory is used for sub path."""
        cmd = mounting_utils.get_az_mount_cmd(self.container_name,
                                              self.storage_account,
                                              self.mount_path,
                                              self.storage_key,
                                              _bucket_sub_path='data/sub')
        self.assertIn('--subdirectory=data/sub/', cmd)

    def test_no_sub_path(self):
        """Verify no --subdirectory when sub path is None."""
        cmd = mounting_utils.get_az_mount_cmd(self.container_name,
                                              self.storage_account,
                                              self.mount_path,
                                              self.storage_key,
                                              _bucket_sub_path=None)
        self.assertNotIn('--subdirectory', cmd)


class TestR2MountCmdDefaults(unittest.TestCase):
    """Test default options for R2 mount commands (goofys + rclone)."""

    def setUp(self):
        self.credentials_path = '/path/to/credentials'
        self.profile_name = 'r2-profile'
        self.endpoint_url = 'https://account.r2.cloudflarestorage.com'
        self.bucket_name = 'test-bucket'
        self.mount_path = '/mnt/test'

    def test_goofys_default_options(self):
        """Verify goofys branch includes all default flags."""
        cmd = mounting_utils.get_r2_mount_cmd(self.credentials_path,
                                              self.profile_name,
                                              self.endpoint_url,
                                              self.bucket_name, self.mount_path)
        self.assertIn('-o allow_other', cmd)
        self.assertIn('--stat-cache-ttl 5s', cmd)
        self.assertIn('--type-cache-ttl 5s', cmd)
        self.assertIn(f'--endpoint {self.endpoint_url}', cmd)

    def test_rclone_default_options(self):
        """Verify rclone branch includes all default flags."""
        cmd = mounting_utils.get_r2_mount_cmd(self.credentials_path,
                                              self.profile_name,
                                              self.endpoint_url,
                                              self.bucket_name, self.mount_path)
        self.assertIn('--daemon --allow-other', cmd)
        self.assertIn(f'--s3-endpoint {self.endpoint_url}', cmd)

    def test_credentials_env_vars(self):
        """Verify AWS credential environment variables are set."""
        cmd = mounting_utils.get_r2_mount_cmd(self.credentials_path,
                                              self.profile_name,
                                              self.endpoint_url,
                                              self.bucket_name, self.mount_path)
        self.assertIn(f'AWS_SHARED_CREDENTIALS_FILE={self.credentials_path}',
                      cmd)
        self.assertIn(f'AWS_PROFILE={self.profile_name}', cmd)

    def test_sub_path(self):
        """Verify sub path is formatted correctly."""
        cmd = mounting_utils.get_r2_mount_cmd(self.credentials_path,
                                              self.profile_name,
                                              self.endpoint_url,
                                              self.bucket_name, self.mount_path,
                                              'data/sub')
        self.assertIn(f'{self.bucket_name}:data/sub', cmd)

    def test_fusermount3_setup(self):
        """Verify fusermount3 setup in rclone branch."""
        cmd = mounting_utils.get_r2_mount_cmd(self.credentials_path,
                                              self.profile_name,
                                              self.endpoint_url,
                                              self.bucket_name, self.mount_path)
        self.assertIn(mounting_utils.FUSE3_INSTALL_CMD, cmd)
        self.assertIn(mounting_utils.FUSERMOUNT3_SOFT_LINK_CMD, cmd)


class TestNebiusMountCmdDefaults(unittest.TestCase):
    """Test default options for Nebius mount commands (goofys + rclone)."""

    def setUp(self):
        self.profile_name = 'nebius-profile'
        self.endpoint_url = 'https://storage.ai.nebius.cloud'
        self.bucket_name = 'test-bucket'
        self.mount_path = '/mnt/test'

    def test_goofys_default_options(self):
        """Verify goofys branch includes all default flags."""
        cmd = mounting_utils.get_nebius_mount_cmd(self.profile_name,
                                                  self.bucket_name,
                                                  self.endpoint_url,
                                                  self.mount_path)
        self.assertIn('-o allow_other', cmd)
        self.assertIn('--stat-cache-ttl 5s', cmd)
        self.assertIn('--type-cache-ttl 5s', cmd)
        self.assertIn(f'--endpoint {self.endpoint_url}', cmd)

    def test_rclone_default_options(self):
        """Verify rclone branch includes all default flags."""
        cmd = mounting_utils.get_nebius_mount_cmd(self.profile_name,
                                                  self.bucket_name,
                                                  self.endpoint_url,
                                                  self.mount_path)
        self.assertIn('--daemon --allow-other', cmd)
        self.assertIn(f'--s3-endpoint {self.endpoint_url}', cmd)

    def test_aws_profile(self):
        """Verify AWS_PROFILE is set for Nebius credentials."""
        cmd = mounting_utils.get_nebius_mount_cmd(self.profile_name,
                                                  self.bucket_name,
                                                  self.endpoint_url,
                                                  self.mount_path)
        self.assertIn(f'AWS_PROFILE={self.profile_name}', cmd)

    def test_sub_path(self):
        """Verify sub path is formatted correctly."""
        cmd = mounting_utils.get_nebius_mount_cmd(self.profile_name,
                                                  self.bucket_name,
                                                  self.endpoint_url,
                                                  self.mount_path, 'data/sub')
        self.assertIn(f'{self.bucket_name}:data/sub', cmd)


class TestCoreweaveMountCmdDefaults(unittest.TestCase):
    """Test default options for CoreWeave mount commands."""

    def setUp(self):
        self.credentials_path = '/path/to/cw-credentials'
        self.profile_name = 'cw-profile'
        self.endpoint_url = 'https://object.ord1.coreweave.com'
        self.bucket_name = 'test-bucket'
        self.mount_path = '/mnt/test'

    def test_goofys_default_options(self):
        """Verify goofys branch includes CoreWeave-specific defaults."""
        cmd = mounting_utils.get_coreweave_mount_cmd(self.credentials_path,
                                                     self.profile_name,
                                                     self.bucket_name,
                                                     self.endpoint_url,
                                                     self.mount_path)
        self.assertIn('-o allow_other', cmd)
        self.assertIn('--stat-cache-ttl 5s', cmd)
        self.assertIn('--type-cache-ttl 5s', cmd)
        self.assertIn(f'--endpoint {self.endpoint_url}', cmd)
        # CoreWeave-specific: --subdomain for goofys
        self.assertIn('--subdomain', cmd)

    def test_rclone_default_options(self):
        """Verify rclone branch includes CoreWeave-specific defaults."""
        cmd = mounting_utils.get_coreweave_mount_cmd(self.credentials_path,
                                                     self.profile_name,
                                                     self.bucket_name,
                                                     self.endpoint_url,
                                                     self.mount_path)
        self.assertIn('--daemon --allow-other', cmd)
        self.assertIn(f'--s3-endpoint {self.endpoint_url}', cmd)
        # CoreWeave-specific: force path style false for rclone
        self.assertIn('--s3-force-path-style=false', cmd)

    def test_credentials_env_vars(self):
        """Verify AWS credential environment variables are set."""
        cmd = mounting_utils.get_coreweave_mount_cmd(self.credentials_path,
                                                     self.profile_name,
                                                     self.bucket_name,
                                                     self.endpoint_url,
                                                     self.mount_path)
        self.assertIn(f'AWS_SHARED_CREDENTIALS_FILE={self.credentials_path}',
                      cmd)
        self.assertIn(f'AWS_PROFILE={self.profile_name}', cmd)

    def test_sub_path(self):
        """Verify sub path is formatted correctly."""
        cmd = mounting_utils.get_coreweave_mount_cmd(
            self.credentials_path, self.profile_name, self.bucket_name,
            self.endpoint_url, self.mount_path, 'data/sub')
        self.assertIn(f'{self.bucket_name}:data/sub', cmd)

    def test_fusermount3_setup(self):
        """Verify fusermount3 setup in rclone branch."""
        cmd = mounting_utils.get_coreweave_mount_cmd(self.credentials_path,
                                                     self.profile_name,
                                                     self.bucket_name,
                                                     self.endpoint_url,
                                                     self.mount_path)
        self.assertIn(mounting_utils.FUSE3_INSTALL_CMD, cmd)
        self.assertIn(mounting_utils.FUSERMOUNT3_SOFT_LINK_CMD, cmd)


class TestCosMountCmdDefaults(unittest.TestCase):
    """Test default options for IBM COS mount commands (rclone)."""

    def setUp(self):
        self.rclone_config = '[cos-profile]\ntype = s3\nprovider = IBMCOS'
        self.profile_name = 'cos-profile'
        self.bucket_name = 'test-bucket'
        self.mount_path = '/mnt/test'

    def test_rclone_default_options(self):
        """Verify rclone mount includes --daemon flag."""
        cmd = mounting_utils.get_cos_mount_cmd(self.rclone_config,
                                               self.profile_name,
                                               self.bucket_name,
                                               self.mount_path)
        self.assertIn('--daemon', cmd)

    def test_rclone_mount_structure(self):
        """Verify rclone mount command structure."""
        cmd = mounting_utils.get_cos_mount_cmd(self.rclone_config,
                                               self.profile_name,
                                               self.bucket_name,
                                               self.mount_path)
        self.assertIn('rclone mount', cmd)
        self.assertIn(f'{self.profile_name}:', cmd)
        self.assertIn(self.mount_path, cmd)

    def test_rclone_config_setup(self):
        """Verify rclone config is written before mount."""
        cmd = mounting_utils.get_cos_mount_cmd(self.rclone_config,
                                               self.profile_name,
                                               self.bucket_name,
                                               self.mount_path)
        self.assertIn('mkdir -p', cmd)
        self.assertIn(self.rclone_config, cmd)

    def test_fusermount3_setup(self):
        """Verify fusermount3 setup for rclone."""
        cmd = mounting_utils.get_cos_mount_cmd(self.rclone_config,
                                               self.profile_name,
                                               self.bucket_name,
                                               self.mount_path)
        self.assertIn(mounting_utils.FUSE3_INSTALL_CMD, cmd)
        self.assertIn(mounting_utils.FUSERMOUNT3_SOFT_LINK_CMD, cmd)


class TestOciMountCmdDefaults(unittest.TestCase):
    """Test default options for OCI mount commands (rclone)."""

    def setUp(self):
        self.mount_path = '/mnt/test'
        self.store_name = 'test-bucket'
        self.region = 'us-ashburn-1'
        self.namespace = 'test-namespace'
        self.compartment = 'ocid1.compartment.oc1..test'
        self.config_file = '~/.oci/config'
        self.config_profile = 'DEFAULT'

    def test_rclone_default_options(self):
        """Verify rclone mount includes default flags."""
        cmd = mounting_utils.get_oci_mount_cmd(self.mount_path, self.store_name,
                                               self.region, self.namespace,
                                               self.compartment,
                                               self.config_file,
                                               self.config_profile)
        self.assertIn('--daemon', cmd)
        self.assertIn('--allow-non-empty', cmd)

    def test_rclone_config_create(self):
        """Verify rclone config is created for OCI."""
        cmd = mounting_utils.get_oci_mount_cmd(self.mount_path, self.store_name,
                                               self.region, self.namespace,
                                               self.compartment,
                                               self.config_file,
                                               self.config_profile)
        self.assertIn('rclone config create', cmd)
        self.assertIn(f'oos_{self.store_name}', cmd)
        self.assertIn('oracleobjectstorage', cmd)
        self.assertIn('provider user_principal_auth', cmd)
        self.assertIn(f'namespace {self.namespace}', cmd)
        self.assertIn(f'compartment {self.compartment}', cmd)
        self.assertIn(f'region {self.region}', cmd)

    def test_oci_config_file_params(self):
        """Verify OCI config file parameters are passed."""
        cmd = mounting_utils.get_oci_mount_cmd(self.mount_path, self.store_name,
                                               self.region, self.namespace,
                                               self.compartment,
                                               self.config_file,
                                               self.config_profile)
        self.assertIn(f'oci-config-file {self.config_file}', cmd)
        self.assertIn(f'oci-config-profile {self.config_profile}', cmd)

    def test_fusermount3_symlink(self):
        """Verify fusermount3 symlink setup."""
        cmd = mounting_utils.get_oci_mount_cmd(self.mount_path, self.store_name,
                                               self.region, self.namespace,
                                               self.compartment,
                                               self.config_file,
                                               self.config_profile)
        self.assertIn('fusermount3', cmd)
        self.assertIn('ln -s /bin/fusermount /bin/fusermount3', cmd)

    def test_mount_path_chown(self):
        """Verify mount path ownership is set."""
        cmd = mounting_utils.get_oci_mount_cmd(self.mount_path, self.store_name,
                                               self.region, self.namespace,
                                               self.compartment,
                                               self.config_file,
                                               self.config_profile)
        self.assertIn(f'sudo chown -R `whoami` {self.mount_path}', cmd)

    def test_rclone_mount_command(self):
        """Verify final rclone mount command structure."""
        cmd = mounting_utils.get_oci_mount_cmd(self.mount_path, self.store_name,
                                               self.region, self.namespace,
                                               self.compartment,
                                               self.config_file,
                                               self.config_profile)
        self.assertIn(
            f'rclone mount oos_{self.store_name}:{self.store_name} '
            f'{self.mount_path}', cmd)


class TestS3MountOptions(unittest.TestCase):
    """Test mount_options parameter for S3 mount commands."""

    def setUp(self):
        self.bucket_name = 'test-bucket'
        self.mount_path = '/mnt/test'

    def test_no_mount_options(self):
        """Without mount_options the command is unchanged."""
        cmd_default = mounting_utils.get_s3_mount_cmd(self.bucket_name,
                                                      self.mount_path)
        cmd_none = mounting_utils.get_s3_mount_cmd(self.bucket_name,
                                                   self.mount_path,
                                                   mount_options=None)
        self.assertEqual(cmd_default, cmd_none)

    def test_mount_options_in_goofys(self):
        """mount_options appears in goofys branch."""
        cmd = mounting_utils.get_s3_mount_cmd(self.bucket_name,
                                              self.mount_path,
                                              mount_options='--my-flag val')
        self.assertIn('--my-flag val', cmd)
        # Should appear after mount_path in goofys branch
        self.assertIn(f'{self.mount_path} --my-flag val', cmd)

    def test_mount_options_in_rclone(self):
        """mount_options appears in rclone branch."""
        cmd = mounting_utils.get_s3_mount_cmd(self.bucket_name,
                                              self.mount_path,
                                              mount_options='--my-flag val')
        self.assertIn('--s3-env-auth=true --my-flag val', cmd)

    def test_mount_options_with_sub_path(self):
        """mount_options works with sub_path."""
        cmd = mounting_utils.get_s3_mount_cmd(self.bucket_name,
                                              self.mount_path,
                                              _bucket_sub_path='sub/dir',
                                              mount_options='--extra')
        self.assertIn(':sub/dir', cmd)
        self.assertIn('--extra', cmd)


class TestGcsMountOptions(unittest.TestCase):
    """Test mount_options parameter for GCS mount commands."""

    def setUp(self):
        self.bucket_name = 'test-bucket'
        self.mount_path = '/mnt/test'

    def test_no_mount_options(self):
        """Without mount_options the command is unchanged."""
        cmd_default = mounting_utils.get_gcs_mount_cmd(self.bucket_name,
                                                       self.mount_path)
        cmd_none = mounting_utils.get_gcs_mount_cmd(self.bucket_name,
                                                    self.mount_path,
                                                    mount_options=None)
        self.assertEqual(cmd_default, cmd_none)

    def test_mount_options_appended(self):
        """mount_options is appended after bucket and mount_path."""
        opts = '--max-conns-per-host 20 --stat-cache-capacity 8192'
        cmd = mounting_utils.get_gcs_mount_cmd(self.bucket_name,
                                               self.mount_path,
                                               mount_options=opts)
        self.assertIn(opts, cmd)
        self.assertIn(f'{self.mount_path} {opts}', cmd)

    def test_default_options_preserved(self):
        """Default gcsfuse options are still present with mount_options."""
        cmd = mounting_utils.get_gcs_mount_cmd(self.bucket_name,
                                               self.mount_path,
                                               mount_options='--extra')
        self.assertIn('--debug_fuse_errors', cmd)
        self.assertIn('-o allow_other', cmd)
        self.assertIn('--implicit-dirs', cmd)


class TestAzMountOptions(unittest.TestCase):
    """Test mount_options parameter for Azure blobfuse2 mount commands."""

    def setUp(self):
        self.container_name = 'test-container'
        self.storage_account = 'teststorage'
        self.mount_path = '/mnt/test'
        self.storage_key = 'dGVzdGtleQ=='

    def test_no_mount_options(self):
        """Without mount_options the default --tmp-path is present."""
        cmd = mounting_utils.get_az_mount_cmd(self.container_name,
                                              self.storage_account,
                                              self.mount_path, self.storage_key)
        self.assertIn('--tmp-path', cmd)
        expected_cache = mounting_utils._BLOBFUSE_CACHE_DIR.format(  # pylint: disable=protected-access
            storage_account_name=self.storage_account,
            container_name=self.container_name)
        self.assertIn(f'--tmp-path {expected_cache}_', cmd)

    def test_mount_options_appended(self):
        """Custom mount_options are appended to blobfuse2 command."""
        opts = '--file-cache-timeout-in-seconds=0 --cache-size-mb=4096'
        cmd = mounting_utils.get_az_mount_cmd(self.container_name,
                                              self.storage_account,
                                              self.mount_path,
                                              self.storage_key,
                                              mount_options=opts)
        self.assertIn(opts, cmd)
        # Default --tmp-path should still be present
        self.assertIn('--tmp-path', cmd)

    def test_custom_tmp_path_overrides_default(self):
        """When mount_options includes --tmp-path, default is omitted."""
        opts = '--tmp-path /custom/cache'
        cmd = mounting_utils.get_az_mount_cmd(self.container_name,
                                              self.storage_account,
                                              self.mount_path,
                                              self.storage_key,
                                              mount_options=opts)
        self.assertIn('--tmp-path /custom/cache', cmd)
        # Default cache path should NOT be present
        expected_cache = mounting_utils._BLOBFUSE_CACHE_DIR.format(  # pylint: disable=protected-access
            storage_account_name=self.storage_account,
            container_name=self.container_name)
        self.assertNotIn(f'--tmp-path {expected_cache}_', cmd)

    def test_default_options_preserved(self):
        """Default blobfuse2 options are still present with mount_options."""
        cmd = mounting_utils.get_az_mount_cmd(self.container_name,
                                              self.storage_account,
                                              self.mount_path,
                                              self.storage_key,
                                              mount_options='--extra')
        self.assertIn('--no-symlinks', cmd)
        self.assertIn(f'--container-name {self.container_name}', cmd)

    def test_none_mount_options_identical(self):
        """Explicit None mount_options is same as default."""
        cmd_default = mounting_utils.get_az_mount_cmd(self.container_name,
                                                      self.storage_account,
                                                      self.mount_path,
                                                      self.storage_key)
        cmd_none = mounting_utils.get_az_mount_cmd(self.container_name,
                                                   self.storage_account,
                                                   self.mount_path,
                                                   self.storage_key,
                                                   mount_options=None)
        self.assertEqual(cmd_default, cmd_none)


class TestR2MountOptions(unittest.TestCase):
    """Test mount_options parameter for R2 mount commands."""

    def setUp(self):
        self.credentials_path = '/path/to/credentials'
        self.profile_name = 'r2-profile'
        self.endpoint_url = 'https://account.r2.cloudflarestorage.com'
        self.bucket_name = 'test-bucket'
        self.mount_path = '/mnt/test'

    def test_no_mount_options(self):
        """Without mount_options the command is unchanged."""
        cmd_default = mounting_utils.get_r2_mount_cmd(self.credentials_path,
                                                      self.profile_name,
                                                      self.endpoint_url,
                                                      self.bucket_name,
                                                      self.mount_path)
        cmd_none = mounting_utils.get_r2_mount_cmd(self.credentials_path,
                                                   self.profile_name,
                                                   self.endpoint_url,
                                                   self.bucket_name,
                                                   self.mount_path,
                                                   mount_options=None)
        self.assertEqual(cmd_default, cmd_none)

    def test_mount_options_in_both_branches(self):
        """mount_options appears in both goofys and rclone branches."""
        cmd = mounting_utils.get_r2_mount_cmd(self.credentials_path,
                                              self.profile_name,
                                              self.endpoint_url,
                                              self.bucket_name,
                                              self.mount_path,
                                              mount_options='--my-opt')
        # Should appear twice: once in rclone, once in goofys
        self.assertEqual(cmd.count('--my-opt'), 2)


class TestNebiusMountOptions(unittest.TestCase):
    """Test mount_options parameter for Nebius mount commands."""

    def setUp(self):
        self.profile_name = 'nebius-profile'
        self.endpoint_url = 'https://storage.ai.nebius.cloud'
        self.bucket_name = 'test-bucket'
        self.mount_path = '/mnt/test'

    def test_no_mount_options(self):
        """Without mount_options the command is unchanged."""
        cmd_default = mounting_utils.get_nebius_mount_cmd(
            self.profile_name, self.bucket_name, self.endpoint_url,
            self.mount_path)
        cmd_none = mounting_utils.get_nebius_mount_cmd(self.profile_name,
                                                       self.bucket_name,
                                                       self.endpoint_url,
                                                       self.mount_path,
                                                       mount_options=None)
        self.assertEqual(cmd_default, cmd_none)

    def test_mount_options_in_both_branches(self):
        """mount_options appears in both goofys and rclone branches."""
        cmd = mounting_utils.get_nebius_mount_cmd(self.profile_name,
                                                  self.bucket_name,
                                                  self.endpoint_url,
                                                  self.mount_path,
                                                  mount_options='--my-opt')
        self.assertEqual(cmd.count('--my-opt'), 2)


class TestCoreweaveMountOptions(unittest.TestCase):
    """Test mount_options parameter for CoreWeave mount commands."""

    def setUp(self):
        self.credentials_path = '/path/to/cw-credentials'
        self.profile_name = 'cw-profile'
        self.endpoint_url = 'https://object.ord1.coreweave.com'
        self.bucket_name = 'test-bucket'
        self.mount_path = '/mnt/test'

    def test_no_mount_options(self):
        """Without mount_options the command is unchanged."""
        cmd_default = mounting_utils.get_coreweave_mount_cmd(
            self.credentials_path, self.profile_name, self.bucket_name,
            self.endpoint_url, self.mount_path)
        cmd_none = mounting_utils.get_coreweave_mount_cmd(self.credentials_path,
                                                          self.profile_name,
                                                          self.bucket_name,
                                                          self.endpoint_url,
                                                          self.mount_path,
                                                          mount_options=None)
        self.assertEqual(cmd_default, cmd_none)

    def test_mount_options_in_both_branches(self):
        """mount_options appears in both goofys and rclone branches."""
        cmd = mounting_utils.get_coreweave_mount_cmd(self.credentials_path,
                                                     self.profile_name,
                                                     self.bucket_name,
                                                     self.endpoint_url,
                                                     self.mount_path,
                                                     mount_options='--my-opt')
        self.assertEqual(cmd.count('--my-opt'), 2)


class TestCosMountOptions(unittest.TestCase):
    """Test mount_options parameter for IBM COS mount commands."""

    def setUp(self):
        self.rclone_config = '[cos-profile]\ntype = s3\nprovider = IBMCOS'
        self.profile_name = 'cos-profile'
        self.bucket_name = 'test-bucket'
        self.mount_path = '/mnt/test'

    def test_no_mount_options(self):
        """Without mount_options the command is unchanged."""
        cmd_default = mounting_utils.get_cos_mount_cmd(self.rclone_config,
                                                       self.profile_name,
                                                       self.bucket_name,
                                                       self.mount_path)
        cmd_none = mounting_utils.get_cos_mount_cmd(self.rclone_config,
                                                    self.profile_name,
                                                    self.bucket_name,
                                                    self.mount_path,
                                                    mount_options=None)
        self.assertEqual(cmd_default, cmd_none)

    def test_mount_options_appended(self):
        """mount_options is appended after --daemon."""
        cmd = mounting_utils.get_cos_mount_cmd(
            self.rclone_config,
            self.profile_name,
            self.bucket_name,
            self.mount_path,
            mount_options='--vfs-cache-mode full')
        self.assertIn('--daemon --vfs-cache-mode full', cmd)


class TestOciMountOptions(unittest.TestCase):
    """Test mount_options parameter for OCI mount commands."""

    def setUp(self):
        self.mount_path = '/mnt/test'
        self.store_name = 'test-bucket'
        self.region = 'us-ashburn-1'
        self.namespace = 'test-namespace'
        self.compartment = 'ocid1.compartment.oc1..test'
        self.config_file = '~/.oci/config'
        self.config_profile = 'DEFAULT'

    def test_no_mount_options(self):
        """Without mount_options the command is unchanged."""
        cmd_default = mounting_utils.get_oci_mount_cmd(
            self.mount_path, self.store_name, self.region, self.namespace,
            self.compartment, self.config_file, self.config_profile)
        cmd_none = mounting_utils.get_oci_mount_cmd(self.mount_path,
                                                    self.store_name,
                                                    self.region,
                                                    self.namespace,
                                                    self.compartment,
                                                    self.config_file,
                                                    self.config_profile,
                                                    mount_options=None)
        self.assertEqual(cmd_default, cmd_none)

    def test_mount_options_appended(self):
        """mount_options is appended after --allow-non-empty."""
        cmd = mounting_utils.get_oci_mount_cmd(
            self.mount_path,
            self.store_name,
            self.region,
            self.namespace,
            self.compartment,
            self.config_file,
            self.config_profile,
            mount_options='--buffer-size 64M')
        self.assertIn('--allow-non-empty --buffer-size 64M', cmd)


class TestMountBinaryDetection(unittest.TestCase):
    """Test _get_mount_binary for all supported mount tools."""

    def test_goofys_detection(self):
        cmd = 'goofys -o allow_other bucket /mnt/test'
        self.assertEqual(mounting_utils._get_mount_binary(cmd), 'goofys')  # pylint: disable=protected-access

    def test_gcsfuse_detection(self):
        cmd = 'gcsfuse --implicit-dirs bucket /mnt/test'
        self.assertEqual(mounting_utils._get_mount_binary(cmd), 'gcsfuse')  # pylint: disable=protected-access

    def test_blobfuse2_detection(self):
        cmd = 'blobfuse2 --no-symlinks --container-name test /mnt/test'
        self.assertEqual(mounting_utils._get_mount_binary(cmd), 'blobfuse2')  # pylint: disable=protected-access

    def test_rclone_detection(self):
        cmd = 'rclone mount profile:bucket /mnt/test --daemon'
        self.assertEqual(mounting_utils._get_mount_binary(cmd), 'rclone')  # pylint: disable=protected-access


class TestVersionConstants(unittest.TestCase):
    """Test that version constants are defined and consistent."""

    def test_blobfuse2_version_defined(self):
        self.assertIsNotNone(mounting_utils.BLOBFUSE2_VERSION)
        self.assertEqual(mounting_utils.BLOBFUSE2_VERSION, '2.5.2')

    def test_gcsfuse_version_defined(self):
        self.assertIsNotNone(mounting_utils.GCSFUSE_VERSION)
        self.assertEqual(mounting_utils.GCSFUSE_VERSION, '2.2.0')

    def test_rclone_version_defined(self):
        self.assertIsNotNone(mounting_utils.RCLONE_VERSION)
        self.assertEqual(mounting_utils.RCLONE_VERSION, 'v1.68.2')

    def test_blobfuse2_version_in_install_cmd(self):
        cmd = mounting_utils.get_az_mount_install_cmd()
        self.assertIn(mounting_utils.BLOBFUSE2_VERSION, cmd)

    def test_gcsfuse_version_in_install_cmd(self):
        cmd = mounting_utils.get_gcs_mount_install_cmd()
        self.assertIn(mounting_utils.GCSFUSE_VERSION, cmd)

    def test_rclone_version_in_install_cmd(self):
        cmd = mounting_utils.get_rclone_install_cmd()
        self.assertIn(mounting_utils.RCLONE_VERSION, cmd)


class TestDefaultConstantsUsage(unittest.TestCase):
    """Test that default constants are actually used in mount commands."""

    def test_stat_cache_ttl_value(self):
        self.assertEqual(mounting_utils._STAT_CACHE_TTL, '5s')  # pylint: disable=protected-access

    def test_stat_cache_capacity_value(self):
        self.assertEqual(mounting_utils._STAT_CACHE_CAPACITY, 4096)  # pylint: disable=protected-access

    def test_type_cache_ttl_value(self):
        self.assertEqual(mounting_utils._TYPE_CACHE_TTL, '5s')  # pylint: disable=protected-access

    def test_rename_dir_limit_value(self):
        self.assertEqual(mounting_utils._RENAME_DIR_LIMIT, 10000)  # pylint: disable=protected-access

    def test_s3_uses_stat_cache_ttl_constant(self):
        """Verify S3 goofys uses the shared constant, not a hardcoded value."""
        cmd = mounting_utils.get_s3_mount_cmd('bucket', '/mnt')
        self.assertIn(
            f'--stat-cache-ttl {mounting_utils._STAT_CACHE_TTL}',  # pylint: disable=protected-access
            cmd)

    def test_gcs_uses_all_shared_constants(self):
        """Verify GCS uses all shared constants."""
        cmd = mounting_utils.get_gcs_mount_cmd('bucket', '/mnt')
        self.assertIn(
            f'--stat-cache-capacity {mounting_utils._STAT_CACHE_CAPACITY}',  # pylint: disable=protected-access
            cmd)
        self.assertIn(
            f'--stat-cache-ttl {mounting_utils._STAT_CACHE_TTL}',  # pylint: disable=protected-access
            cmd)
        self.assertIn(
            f'--type-cache-ttl {mounting_utils._TYPE_CACHE_TTL}',  # pylint: disable=protected-access
            cmd)
        self.assertIn(
            f'--rename-dir-limit {mounting_utils._RENAME_DIR_LIMIT}',  # pylint: disable=protected-access
            cmd)


if __name__ == '__main__':
    unittest.main()
