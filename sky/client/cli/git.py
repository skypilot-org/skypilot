"""Git utilities for SkyPilot."""
import os
import re
from typing import Dict, List, Optional, Union

import git
import requests

from sky import exceptions
from sky import sky_logging

logger = sky_logging.init_logger(__name__)

GIT_TOKEN_ENV_VAR = 'GIT_TOKEN'
GIT_SSH_KEY_PATH_ENV_VAR = 'GIT_SSH_KEY_PATH'
GIT_SSH_KEY_ENV_VAR = 'GIT_SSH_KEY'
GIT_URL_ENV_VAR = 'GIT_URL'
GIT_COMMIT_HASH_ENV_VAR = 'GIT_COMMIT_HASH'
GIT_BRANCH_ENV_VAR = 'GIT_BRANCH'


class GitUrlInfo:
    """Information extracted from a git URL."""
    def __init__(self, host: str, path: str, protocol: str, user: Optional[str] = None, port: Optional[int] = None):
        self.host = host
        self.path = path  # Repository path (e.g., 'user/repo' or 'org/subgroup/repo')
        self.protocol = protocol  # 'https', 'ssh', 'http'
        self.user = user  # SSH username
        self.port = port

class GitCloneInfo:
    """Information about a git clone."""
    def __init__(self, url: str, envs: Optional[dict] = None, token: Optional[str] = None, ssh_key: Optional[str] = None):
        self.url = url
        self.envs = envs
        self.token = token
        self.ssh_key = ssh_key

class GitRepo:
    """Git utilities for SkyPilot."""

    def __init__(self,
                 repo_url: str,
                 ref: str = 'main',
                 git_token: Optional[str] = None,
                 git_ssh_key_path: Optional[str] = None):
        """Initialize Git utility.

        Args:
            repo_url: Git repository URL.
            ref: Git reference (branch, tag, or commit hash).
            git_token: GitHub token for private repositories.
            git_ssh_key_path: Path to SSH private key for authentication.
        """
        self.repo_url = repo_url
        self.ref = ref
        self.git_token = git_token
        self.git_ssh_key_path = git_ssh_key_path
        self._discovered_ssh_key_path: Optional[str] = None
        
        # Parse URL during initialization to catch format errors early
        self._parsed_url = self._parse_git_url(self.repo_url)

    def _parse_git_url(self, url: str) -> GitUrlInfo:
        """Parse git URL into components.
        
        Supports various git URL formats:
        - HTTPS: https://github.com/user/repo.git
        - SSH: git@github.com:user/repo.git (SCP-like)
        - SSH full: ssh://git@github.com/user/repo.git
        - SSH with port: ssh://git@github.com:2222/user/repo.git
        
        Args:
            url: Git repository URL in any supported format.
            
        Returns:
            GitUrlInfo with parsed components.
            
        Raises:
            exceptions.GitError: If URL format is not supported.
        """
        # Remove trailing .git if present
        clean_url = url.rstrip('/')
        if clean_url.endswith('.git'):
            clean_url = clean_url[:-4]
            
        # Pattern for HTTPS/HTTP URLs
        https_pattern = r'^(https?)://(?:([^@]+)@)?([^:/]+)(?::(\d+))?/(.+)$'
        https_match = re.match(https_pattern, clean_url)
        
        if https_match:
            protocol, user, host, port_str, path = https_match.groups()
            port = int(port_str) if port_str else None
            
            # Validate that path is not empty
            if not path or path == '/':
                raise exceptions.GitError(
                    f'Invalid repository path in URL: {url}')
            
            return GitUrlInfo(
                host=host,
                path=path,
                protocol=protocol,
                user=user,
                port=port
            )
        
        # Pattern for SSH URLs (full format)
        ssh_full_pattern = r'^ssh://(?:([^@]+)@)?([^:/]+)(?::(\d+))?/(.+)$'
        ssh_full_match = re.match(ssh_full_pattern, clean_url)
        
        if ssh_full_match:
            user, host, port_str, path = ssh_full_match.groups()
            port = int(port_str) if port_str else None
            
            # Validate that path is not empty
            if not path or path == '/':
                raise exceptions.GitError(
                    f'Invalid repository path in SSH URL: {url}')
            
            return GitUrlInfo(
                host=host,
                path=path,
                protocol='ssh',
                user=user,
                port=port
            )
        
        # Pattern for SSH SCP-like format (exclude URLs with ://)
        scp_pattern = r'^(?:([^@]+)@)?([^:/]+):(.+)$'
        scp_match = re.match(scp_pattern, clean_url)
        
        # Make sure it's not a URL with protocol (should not contain ://)
        if scp_match and '://' not in clean_url:
            user, host, path = scp_match.groups()
            
            # Validate that path is not empty
            if not path:
                raise exceptions.GitError(
                    f'Invalid repository path in SSH URL: {url}')
            
            return GitUrlInfo(
                host=host,
                path=path,
                protocol='ssh',
                user=user,
                port=None
            )
        
        raise exceptions.GitError(
            f'Unsupported git URL format: {url}. '
            'Supported formats: https://host/owner/repo, '
            'ssh://user@host/owner/repo, user@host:owner/repo')

    def get_https_url(self, with_token: bool = False) -> str:
        """Get HTTPS URL for the repository.

        Args:
            with_token: If True, includes token in URL for authentication

        Returns:
            HTTPS URL string.
        """
        port_str = f':{self._parsed_url.port}' if self._parsed_url.port else ''
        path = self._parsed_url.path
        # Remove .git suffix if present (but not individual characters)
        if path.endswith('.git'):
            path = path[:-4]
        
        if with_token and self.git_token:
            return f'https://{self.git_token}@{self._parsed_url.host}{port_str}/{path}.git'
        else:
            return f'https://{self._parsed_url.host}{port_str}/{path}.git'

    def get_ssh_url(self) -> str:
        """Get SSH URL for the repository in full format.
        
        Returns:
            SSH URL string in full format.
        """
        # Use original user from URL, or default to 'git'
        ssh_user = self._parsed_url.user or 'git'
        port_str = f':{self._parsed_url.port}' if self._parsed_url.port else ''
        path = self._parsed_url.path
        # Remove .git suffix if present (but not individual characters)
        if path.endswith('.git'):
            path = path[:-4]
        return f'ssh://{ssh_user}@{self._parsed_url.host}{port_str}/{path}.git'

    def get_repo_clone_info(self) -> GitCloneInfo:
        """Validate the repository access with comprehensive authentication strategies.

        This method implements a sequential validation approach:
        1. Try public access (no authentication)
        2. If has token, try token access
        3. If has ssh_key_path, try ssh access
        4. Find default ssh credential and try ssh access

        Returns:
            GitCloneInfo instance with successful access method.

        Raises:
            exceptions.GitError: If the git URL format is invalid or
              the repository cannot be accessed.
        """
        logger.info(f'Validating access to {self._parsed_url.host}/{self._parsed_url.path}')

        # Step 1: Try public access first (most common case)
        try:
            https_url = self.get_https_url()
            logger.debug(f'Trying public HTTPS access to {https_url}')
            
            # Use Git HTTP protocol endpoint to check public access
            # This is more reliable than git ls-remote as it doesn't use local git config
            # Construct the info/refs endpoint URL as per Git HTTP protocol
            info_refs_url = f"{https_url.rstrip('/')}/info/refs?service=git-upload-pack"
            
            # Make a simple HTTP request without any authentication
            response = requests.get(info_refs_url, 
                                   timeout=10,
                                   allow_redirects=True,
                                   # Ensure no local credentials are used
                                   auth=None)
            
            if response.status_code == 200:                
                return GitCloneInfo(url=https_url)
        except Exception as e:
            logger.debug(f'Public access failed: {str(e)}')

        # Step 2: Try with token if provided
        if self.git_token:
            try:
                https_url = self.get_https_url()
                auth_url = self.get_https_url(with_token=True)
                logger.debug(f'Trying token authentication to {https_url}')
                git_cmd = git.cmd.Git()
                git_cmd.ls_remote(auth_url)
                logger.info('Successfully validated repository access using token authentication')
                return GitCloneInfo(url=https_url, token=self.git_token)
            except Exception as e:
                logger.debug(f'Token access failed: {str(e)}')
                raise exceptions.GitError(
                    f'Failed to access repository {self.repo_url} using token authentication. '
                    f'Please verify your token and repository access permissions.\n'
                    f'Original error: {str(e)}') from e

        # Step 3: Try with SSH key if provided
        if self.git_ssh_key_path:
            try:
                ssh_url = self.get_ssh_url()
                key_path = os.path.expanduser(self.git_ssh_key_path)
                
                # Validate SSH key before using it
                if not os.path.exists(key_path):
                    raise exceptions.GitError(
                        f'SSH key not found at path: {self.git_ssh_key_path}')
                
                # Check key permissions
                key_stat = os.stat(key_path)
                if key_stat.st_mode & 0o077:
                    logger.warning(
                        f'SSH key {key_path} has too open permissions. '
                        f'Recommended: chmod 600 {key_path}')
                
                # Check if it's a valid private key
                try:
                    with open(key_path, 'r') as f:
                        key_content = f.read()
                        if not (key_content.startswith('-----BEGIN') and 'PRIVATE KEY' in key_content):
                            logger.warning(f'SSH key {key_path} may not be a valid private key format')
                except Exception as e:
                    logger.warning(f'Could not validate SSH key format: {str(e)}')
                
                ssh_env = {
                    'GIT_SSH_COMMAND': f'ssh -F none -i {key_path} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o IdentitiesOnly=yes'
                }
                
                logger.debug(f'Trying SSH key authentication to {ssh_url}')
                git_cmd = git.cmd.Git()
                git_cmd.update_environment(**ssh_env)
                git_cmd.ls_remote(ssh_url)
                logger.info('Successfully validated repository access using SSH key authentication')
                return GitCloneInfo(url=ssh_url, ssh_key=key_path, envs=ssh_env)
                
            except Exception as e:
                logger.debug(f'SSH key access failed: {str(e)}')
                raise exceptions.GitError(
                    f'Failed to access repository {self.repo_url} using SSH key authentication. '
                    f'Please verify your SSH key and repository access permissions.\n'
                    f'Original error: {str(e)}') from e

        # Step 4: Try SSH with discovered/system default keys (if original URL was SSH)
        if self._parsed_url.protocol == 'ssh':
            try:
                ssh_url = self.get_ssh_url()
                
                # Try to discover SSH key from user's configuration and defaults
                discovered_key = self._discover_ssh_key()
                if discovered_key:
                    ssh_env = {
                        'GIT_SSH_COMMAND': f'ssh -F none -i {discovered_key} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o IdentitiesOnly=yes'
                    }
                    
                    logger.debug(f'Trying {ssh_url} with {discovered_key}')
                    git_cmd = git.cmd.Git()
                    git_cmd.update_environment(**ssh_env)
                    git_cmd.ls_remote(ssh_url)
                    logger.info(f'Successfully validated repository {ssh_url} access using {discovered_key}')
                    # Store the discovered key for later use
                    self._discovered_ssh_key_path = discovered_key
                    return GitCloneInfo(url=ssh_url, ssh_key=discovered_key, envs=ssh_env)
                    
            except Exception as e:
                raise exceptions.GitError(
                    f'Failed to access repository {self.repo_url}. '
                    f'Original error: {str(e)}') from e

        # If we get here, no authentication methods are available
        raise exceptions.GitError(
            f'Failed to access repository {self.repo_url}. '
            'If this is a private repository, please provide authentication'
            f' using either: GIT_TOKEN for token-based access, or'
            f' GIT_SSH_KEY_PATH for SSH access.')





    def _parse_ssh_config(self) -> Optional[str]:
        """Parse SSH config file to find IdentityFile for the target host.
        
        Returns:
            Path to SSH private key specified in config, or None if not found.
        """
        ssh_config_path = os.path.expanduser('~/.ssh/config')
        if not os.path.exists(ssh_config_path):
            logger.debug('SSH config file ~/.ssh/config does not exist')
            return None
        
        try:
            # Try to use paramiko's SSH config parser if available
            try:
                import paramiko
                ssh_config = paramiko.SSHConfig()
                with open(ssh_config_path, 'r') as f:
                    ssh_config.parse(f)
                
                # Get config for the target host
                host_config = ssh_config.lookup(self._parsed_url.host)
                
                # Look for identity files in the config
                identity_files: Union[str, List[str]] = host_config.get('identityfile', [])
                if not isinstance(identity_files, list):
                    identity_files = [identity_files]
                
                # Find the first existing identity file
                for identity_file in identity_files:
                    key_path = os.path.expanduser(identity_file)
                    if os.path.exists(key_path):
                        logger.debug(f'Found SSH key in config for {self._parsed_url.host}: {key_path}')
                        return key_path
                
                logger.debug(f'No valid SSH keys found in config for host: {self._parsed_url.host}')
                return None
                
            except ImportError:
                # Fall back to manual parsing if paramiko is not available
                logger.debug('paramiko not available, falling back to manual SSH config parsing')
                # return self._parse_ssh_config_manual()
                return None
                
        except Exception as e:
            logger.debug(f'Error parsing SSH config: {str(e)}')
            return None
    
    def _parse_ssh_config_manual(self) -> Optional[str]:
        """Manual SSH config parsing fallback.
        
        Returns:
            Path to SSH private key specified in config, or None if not found.
        """
        ssh_config_path = os.path.expanduser('~/.ssh/config')
        
        try:
            with open(ssh_config_path, 'r') as f:
                config_content = f.read()
            
            # SSH config parsing - process Host blocks and find matching entries
            hosts_config: Dict[str, Dict[str, List[str]]] = {}
            current_host = None
            
            # First pass: parse config into structured data
            for line in config_content.split('\n'):
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                # Parse Host directive
                if line.lower().startswith('host '):
                    current_host = line.split(None, 1)[1].strip()
                    if current_host not in hosts_config:
                        hosts_config[current_host] = {'identity_files': []}
                    continue
                
                # Parse IdentityFile directive for current host
                if line.lower().startswith('identityfile ') and current_host:
                    identity_file = line.split(None, 1)[1].strip()
                    # Remove quotes if present
                    if identity_file.startswith('"') and identity_file.endswith('"'):
                        identity_file = identity_file[1:-1]
                    hosts_config[current_host]['identity_files'].append(identity_file)
            
            # Second pass: find matching host and return first existing key
            for host_pattern, config in hosts_config.items():
                if self._host_matches_pattern(host_pattern, self._parsed_url.host):
                    for identity_file in config['identity_files']:
                        key_path = os.path.expanduser(identity_file)
                        if os.path.exists(key_path):
                            logger.debug(f'Found SSH key in config for {self._parsed_url.host} (pattern: {host_pattern}): {key_path}')
                            return key_path
            
            logger.debug(f'No SSH config entry found for host: {self._parsed_url.host}')
            return None
            
        except Exception as e:
            logger.debug(f'Error in manual SSH config parsing: {str(e)}')
            return None
    
    def _host_matches_pattern(self, pattern: str, hostname: str) -> bool:
        """Check if hostname matches SSH config host pattern.
        
        Args:
            pattern: Host pattern from SSH config (may contain wildcards)
            hostname: Target hostname
            
        Returns:
            True if hostname matches pattern
        """
        import re

        # Handle special SSH config patterns
        if '*' in pattern or '?' in pattern:
            # Convert SSH pattern to regex
            # Escape special regex characters except * and ?
            escaped_pattern = re.escape(pattern)
            # Replace escaped wildcards with regex equivalents
            regex_pattern = escaped_pattern.replace('\\*', '.*').replace('\\?', '.')
            try:
                return bool(re.match(f'^{regex_pattern}$', hostname))
            except re.error:
                # Fallback to simple string comparison if regex fails
                return pattern == hostname
        else:
            # Exact match
            return pattern == hostname

    def _discover_ssh_key(self) -> Optional[str]:
        """Discover SSH private key using comprehensive strategy.
        
        Strategy:
        1. Check SSH config for host-specific IdentityFile
        2. Search for common SSH key types in ~/.ssh/ directory
        
        Returns:
            Path to discovered SSH private key, or None if not found.
        """
        # Step 1: Check SSH config for host-specific configuration
        config_key = self._parse_ssh_config()
        if config_key:
            logger.debug(f'Using SSH key from config: {config_key}')
            return config_key
        
        # Step 2: Search for default SSH keys
        ssh_dir = os.path.expanduser('~/.ssh')
        if not os.path.exists(ssh_dir):
            logger.debug('SSH directory ~/.ssh does not exist')
            return None
        
        # Common SSH key file names in order of preference
        key_candidates = [
            'id_rsa',        # Most common
            'id_ed25519',    # Modern, recommended
        ]
        
        for key_name in key_candidates:
            private_key_path = os.path.join(ssh_dir, key_name)
            public_key_path = f'{private_key_path}.pub'
            
            # Check if both private and public keys exist
            if not os.path.exists(private_key_path):
                continue
                
            if not os.path.exists(public_key_path):
                logger.debug(f'Private key {private_key_path} exists but public key {public_key_path} is missing')
                continue
            
            # Check private key permissions
            try:
                key_stat = os.stat(private_key_path)
                if key_stat.st_mode & 0o077:
                    logger.warning(f'SSH key {private_key_path} has too open permissions. Consider: chmod 600 {private_key_path}')
                
                # Validate private key format
                with open(private_key_path, 'r') as f:
                    key_content = f.read()
                    if not (key_content.startswith('-----BEGIN') and 'PRIVATE KEY' in key_content):
                        logger.debug(f'SSH key {private_key_path} may not be a valid private key format')
                        continue
                
                logger.debug(f'Discovered default SSH key: {private_key_path}')
                return private_key_path
                
            except Exception as e:
                logger.debug(f'Error checking SSH key {private_key_path}: {str(e)}')
                continue
        
        logger.debug('No suitable SSH keys found in ~/.ssh/')
        return None

    def get_effective_ssh_key_path(self) -> Optional[str]:
        """Get the effective SSH key path that will be used for authentication.
        
        Returns:
            Path to SSH key file that will be used, or None if no SSH key available.
        """
        if self.git_ssh_key_path:
            return os.path.expanduser(self.git_ssh_key_path)
        
        if self._discovered_ssh_key_path is None:
            self._discovered_ssh_key_path = self._discover_ssh_key()
        
        return self._discovered_ssh_key_path

    def is_commit_hash(self) -> bool:
        """Check if the reference is a commit hash.

        Returns:
            True if it's a commit hash, False if it's a branch/tag.

        Raises:
            exceptions.GitError: If the reference is invalid.
        """
        clone_info = self.get_repo_clone_info()
        git_cmd = git.cmd.Git()
        if clone_info.envs:
            git_cmd.update_environment(**clone_info.envs)

        try:
            # Get all remote refs
            refs = git_cmd.ls_remote(clone_info.url).split('\n')
            
            # Collect all commit hashes from refs
            all_commit_hashes = set()
            
            # Check if it's a branch or tag name
            for ref in refs:
                if not ref:
                    continue
                hash_val, ref_name = ref.split('\t')
                
                # Store the commit hash for later validation
                all_commit_hashes.add(hash_val)
                
                # Check if it's a branch
                if ref_name.startswith('refs/heads/') and ref_name[11:] == self.ref:
                    return False
                
                # Check if it's a tag
                if ref_name.startswith('refs/tags/') and ref_name[10:] == self.ref:
                    return False

            # If we get here, it's not a branch or tag name
            # Check if it looks like a commit hash (hex string)
            if len(self.ref) >= 4 and all(c in '0123456789abcdef' for c in self.ref.lower()):
                # First check if it's a complete match with any known commit
                if self.ref in all_commit_hashes:
                    logger.debug(f'Found exact commit hash match: {self.ref}')
                    return True
                
                # Check if it's a prefix match with any known commit
                matching_commits = [h for h in all_commit_hashes if h.startswith(self.ref)]
                if len(matching_commits) == 1:
                    logger.debug(f'Found commit hash prefix match: {self.ref} -> {matching_commits[0]}')
                    return True
                elif len(matching_commits) > 1:
                    # Multiple matches - ambiguous
                    raise exceptions.GitError(
                        f'Ambiguous commit hash {self.ref!r}. '
                        f'Multiple commits match: {", ".join(matching_commits[:5])}...')
                
                # If no match found in ls-remote output, we can't verify the commit exists
                # This could be a valid commit that's not at the tip of any branch/tag
                # We'll assume it's valid if it looks like a commit hash and let git handle validation during clone
                logger.debug(f'Commit hash not found in ls-remote output, assuming valid: {self.ref}')
                logger.warning(f'Cannot verify commit {self.ref} exists - it may be a commit in history not at any branch/tag tip')
                return True

            # If it's not a branch, tag, or hex string, it's invalid
            raise exceptions.GitError(
                f'Git reference {self.ref!r} not found. '
                'Please provide a valid branch, tag, or commit hash.')

        except git.exc.GitCommandError as e:
            if not (self.git_token or self.git_ssh_key_path):
                raise exceptions.GitError(
                    'Failed to check repository. If this is a private '
                    'repository, please provide authentication using either '
                    '--git-token or --git-ssh-key-path.') from e
            raise exceptions.GitError(
                f'Failed to check git reference: {str(e)}') from e

    @property
    def repo_info(self) -> GitUrlInfo:
        """Get parsed repository information."""
        return self._parsed_url 