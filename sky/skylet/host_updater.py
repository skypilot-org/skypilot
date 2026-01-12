"""Host updater for JobGroup networking.

This module provides functionality to keep /etc/hosts updated with resolved
DNS entries for JobGroup service discovery.

The host updater runs as part of skylet and handles:
- K8s: Resolving service DNS names to IPs
- Updating /etc/hosts with the resolved mappings

Configuration sources (checked in order):
1. File: ~/.sky/jobgroup_dns_mappings.json
2. Environment variable: SKYPILOT_JOBGROUP_DNS_MAPPINGS

Logs are written to: ~/.sky/host_updater.log
"""
import json
import logging
import os
import socket
import subprocess
import threading
import time
from typing import Dict, List, Optional, Tuple

from sky import sky_logging

logger = sky_logging.init_logger(__name__)

# Environment variable containing JSON-encoded DNS mappings
JOBGROUP_DNS_MAPPINGS_ENV_VAR = 'SKYPILOT_JOBGROUP_DNS_MAPPINGS'

# File path for DNS mappings (written by controller after launch)
JOBGROUP_DNS_MAPPINGS_FILE = '~/.sky/jobgroup_dns_mappings.json'

# Log file for host updater (for easy observability)
HOST_UPDATER_LOG_FILE = '~/.sky/host_updater.log'

# Marker comment for /etc/hosts entries
HOSTS_MARKER = '# SkyPilot JobGroup'

# Update interval in seconds
UPDATE_INTERVAL_SECONDS = 5


def _setup_file_logger() -> logging.Logger:
    """Set up a dedicated file logger for host updater.

    Returns:
        A logger that writes to the host updater log file.
    """
    file_logger = logging.getLogger('sky.skylet.host_updater.file')
    if file_logger.handlers:
        # Already set up
        return file_logger

    log_path = os.path.expanduser(HOST_UPDATER_LOG_FILE)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    handler = logging.FileHandler(log_path)
    handler.setFormatter(
        logging.Formatter('%(asctime)s [%(levelname)s] %(message)s',
                          datefmt='%Y-%m-%d %H:%M:%S'))
    file_logger.addHandler(handler)
    file_logger.setLevel(logging.DEBUG)
    # Don't propagate to root logger to avoid duplicate logs
    file_logger.propagate = False

    return file_logger


# File logger for detailed host updater logs
_file_logger: Optional[logging.Logger] = None


def _log(level: int, msg: str) -> None:
    """Log to both the main logger and the dedicated file logger."""
    global _file_logger
    if _file_logger is None:
        _file_logger = _setup_file_logger()

    # Log to main skylet logger
    logger.log(level, msg)
    # Also log to dedicated file
    _file_logger.log(level, msg)


class HostUpdater:
    """Background host updater for JobGroup networking.

    Resolves DNS names to IPs and updates /etc/hosts periodically.

    Logs are written to ~/.sky/host_updater.log for easy observability.
    Use 'cat ~/.sky/host_updater.log' or 'tail -f ~/.sky/host_updater.log'
    to view the logs.
    """

    def __init__(self, dns_mappings: List[Tuple[str, str]]):
        """Initialize the host updater.

        Args:
            dns_mappings: List of (dns_name, simple_hostname) tuples.
                dns_name: The DNS name to resolve (e.g., K8s service DNS)
                simple_hostname: The simple hostname to add to /etc/hosts
        """
        self._dns_mappings = dns_mappings
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._current_ips: Dict[str, str] = {}  # hostname -> ip
        self._resolved_count = 0
        self._update_count = 0

    def start(self) -> None:
        """Start the host updater background thread."""
        if self._running:
            _log(logging.WARNING, 'HostUpdater already running')
            return

        if not self._dns_mappings:
            _log(logging.INFO,
                 'No DNS mappings configured, host updater not started')
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        # Log startup with details about what we're monitoring
        _log(logging.INFO,
             f'HostUpdater started with {len(self._dns_mappings)} mappings')
        _log(logging.INFO,
             f'Log file: {os.path.expanduser(HOST_UPDATER_LOG_FILE)}')
        for dns_name, hostname in self._dns_mappings:
            _log(logging.INFO, f'  Monitoring: {dns_name} -> {hostname}')

    def stop(self) -> None:
        """Stop the host updater."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None
        _log(
            logging.INFO,
            f'HostUpdater stopped (resolved {self._resolved_count} times, '
            f'updated /etc/hosts {self._update_count} times)')

    def _run_loop(self) -> None:
        """Main update loop."""
        _log(logging.INFO, 'HostUpdater background loop started')
        while self._running:
            try:
                self._update_hosts()
            except Exception as e:  # pylint: disable=broad-except
                _log(logging.ERROR, f'Error updating hosts: {e}')
            time.sleep(UPDATE_INTERVAL_SECONDS)

    def _update_hosts(self) -> None:
        """Resolve DNS and update /etc/hosts if needed."""
        new_entries = []
        needs_update = False
        resolved_all = True

        for dns_name, hostname in self._dns_mappings:
            ip = self._resolve_dns(dns_name)
            if ip:
                new_entries.append((ip, hostname))
                # Check if IP changed
                if self._current_ips.get(hostname) != ip:
                    old_ip = self._current_ips.get(hostname, 'None')
                    _log(logging.INFO,
                         f'IP resolved for {hostname}: {old_ip} -> {ip}')
                    self._current_ips[hostname] = ip
                    needs_update = True
            else:
                resolved_all = False
                _log(logging.DEBUG, f'Waiting to resolve: {dns_name}')

        if needs_update and new_entries:
            self._write_hosts(new_entries)
            self._resolved_count += 1

        # Log periodic status (every ~60 seconds = 12 intervals)
        if self._resolved_count > 0 and self._resolved_count % 12 == 0:
            status = 'all resolved' if resolved_all else 'some pending'
            _log(
                logging.DEBUG,
                f'Status: {len(self._current_ips)}/{len(self._dns_mappings)} '
                f'hostnames resolved ({status})')

    def _resolve_dns(self, dns_name: str) -> Optional[str]:
        """Resolve a DNS name to IP address.

        Args:
            dns_name: DNS name to resolve.

        Returns:
            IP address string, or None if resolution failed.
        """
        try:
            # Use getaddrinfo for more reliable resolution
            results = socket.getaddrinfo(dns_name, None, socket.AF_INET)
            if results:
                return results[0][4][0]
        except socket.gaierror:
            pass
        return None

    def _write_hosts(self, entries: List[Tuple[str, str]]) -> None:
        """Write entries to /etc/hosts.

        Args:
            entries: List of (ip, hostname) tuples.
        """
        try:
            # Read existing content, filtering out our entries
            with open('/etc/hosts', 'r', encoding='utf-8') as f:
                existing_lines = [
                    line for line in f.readlines() if HOSTS_MARKER not in line
                ]

            # Build new content
            new_lines = existing_lines
            for ip, hostname in entries:
                new_lines.append(f'{ip} {hostname}  {HOSTS_MARKER}\n')

            # Write back using sudo tee (handles K8s mounted /etc/hosts)
            content = ''.join(new_lines)
            proc = subprocess.run(
                ['sudo', 'tee', '/etc/hosts'],
                input=content.encode(),
                capture_output=True,
                check=False,
            )
            if proc.returncode != 0:
                _log(logging.ERROR,
                     f'Failed to write /etc/hosts: {proc.stderr.decode()}')
            else:
                self._update_count += 1
                _log(logging.INFO,
                     f'Updated /etc/hosts with {len(entries)} entries')
                for ip, hostname in entries:
                    _log(logging.INFO, f'  {ip} {hostname}')
        except Exception as e:  # pylint: disable=broad-except
            _log(logging.ERROR, f'Error writing /etc/hosts: {e}')


def get_dns_mappings_from_file() -> List[Tuple[str, str]]:
    """Get DNS mappings from file.

    Returns:
        List of (dns_name, simple_hostname) tuples, or empty list if not found.
    """
    file_path = os.path.expanduser(JOBGROUP_DNS_MAPPINGS_FILE)
    if not os.path.exists(file_path):
        return []

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            # Format: [["dns1", "hostname1"], ["dns2", "hostname2"], ...]
            mappings = json.load(f)
            _log(logging.INFO,
                 f'Loaded {len(mappings)} DNS mappings from {file_path}')
            return [(dns, hostname) for dns, hostname in mappings]
    except (json.JSONDecodeError, TypeError, ValueError, OSError) as e:
        _log(logging.ERROR, f'Failed to read {file_path}: {e}')
        return []


def get_dns_mappings_from_env() -> List[Tuple[str, str]]:
    """Get DNS mappings from environment variable.

    Returns:
        List of (dns_name, simple_hostname) tuples, or empty list if not set.
    """
    env_value = os.environ.get(JOBGROUP_DNS_MAPPINGS_ENV_VAR)
    if not env_value:
        return []

    try:
        # Format: [["dns1", "hostname1"], ["dns2", "hostname2"], ...]
        mappings = json.loads(env_value)
        _log(logging.INFO,
             f'Loaded {len(mappings)} DNS mappings from environment variable')
        return [(dns, hostname) for dns, hostname in mappings]
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        _log(logging.ERROR,
             f'Failed to parse {JOBGROUP_DNS_MAPPINGS_ENV_VAR}: {e}')
        return []


def get_dns_mappings() -> List[Tuple[str, str]]:
    """Get DNS mappings from file or environment variable.

    Checks file first, then environment variable.

    Returns:
        List of (dns_name, simple_hostname) tuples, or empty list if not found.
    """
    # Check file first (written by controller after launch)
    mappings = get_dns_mappings_from_file()
    if mappings:
        return mappings

    # Fallback to environment variable
    return get_dns_mappings_from_env()


def encode_dns_mappings(mappings: List[Tuple[str, str]]) -> str:
    """Encode DNS mappings as JSON string for environment variable.

    Args:
        mappings: List of (dns_name, simple_hostname) tuples.

    Returns:
        JSON-encoded string.
    """
    return json.dumps([[dns, hostname] for dns, hostname in mappings])


# Global host updater instance
_host_updater: Optional[HostUpdater] = None


def start_host_updater() -> bool:
    """Start the global host updater if DNS mappings are configured.

    Checks for DNS mappings from file or environment variable.

    Returns:
        True if host updater was started, False otherwise.
    """
    global _host_updater

    if _host_updater is not None:
        _log(logging.DEBUG, 'Host updater already initialized')
        return True

    mappings = get_dns_mappings()
    if not mappings:
        # Don't log at INFO level to avoid noise when not in a job group
        logger.debug('No DNS mappings found, host updater not needed')
        return False

    _host_updater = HostUpdater(mappings)
    _host_updater.start()
    return True


def stop_host_updater() -> None:
    """Stop the global host updater."""
    global _host_updater

    if _host_updater is not None:
        _host_updater.stop()
        _host_updater = None
