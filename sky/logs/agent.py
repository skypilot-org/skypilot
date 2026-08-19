"""Base class for all logging agents."""
import abc
import os
import shlex
import textwrap
from typing import Any, Dict

from sky.skylet import constants
from sky.utils import resources_utils
from sky.utils import yaml_utils

# Wall-clock backstop for a single `apt-get update`, in seconds.
#
# This is *not* how an unreachable mirror is detected -- Acquire::*::Timeout
# below does that, and reports it in ~30s. This cap only catches a pathological
# hang that apt itself never returns from, so it is deliberately generous:
# mirrors have been observed serving a full index refresh at ~100 kB/s, which
# legitimately takes many minutes, and killing that would fail a launch that
# would otherwise merely have been slow.
#
# Deliberately not applied to `apt-get install`: that would also cap dpkg
# unpack/configure, and a SIGTERM landing mid-dpkg leaves the package database
# needing `dpkg --configure -a`. The fetch phase of an install is already
# bounded by Acquire::*::Timeout.
_APT_UPDATE_TIMEOUT = 600

# Acquire::Retries=0: retrying inside apt multiplies the stall by the number of
# index files it has to fetch. The retry that actually helps is the one that
# changes mirror, which is done below. Acquire::*::Timeout is an *inactivity*
# timeout, not a total transfer deadline, so a slow-but-progressing download is
# not penalised -- only one that has stopped sending data. 5s buys fast
# detection (a fully unreachable mirror is diagnosed in ~30s rather than ~90s)
# while still tolerating a mirror that is merely slow to first byte.
_APT_OPTS = ('-o Acquire::Retries=0 '
             '-o Acquire::http::Timeout=5 '
             '-o Acquire::https::Timeout=5')

# Private, root-owned scratch dir for the fallback apt configuration. Under
# /etc/apt rather than /tmp: /etc/apt is root-owned, so an unprivileged local
# user cannot pre-create these paths as symlinks and redirect a root write.
_APT_FALLBACK_DIR = '/etc/apt/sky-fallback'

# Shell helpers wrapping apt-get for the logging-agent install.
#
# This install runs inline on every node of every cluster launch, so a degraded
# distro mirror is not a cosmetic problem. `apt-get update` alone fetches dozens
# of index files, and Ubuntu's per-region EC2 mirrors
# (`<region>.ec2.archive.ubuntu.com`) have repeatedly become wholly unreachable
# -- they sit behind no CDN, and such outages have recurred without appearing on
# any status page. Two things then go wrong at once: apt spends seconds to
# minutes per file failing to connect, and `apt-get update` still *exits 0* when
# every single source failed to fetch, so nothing surfaces the cause and the
# launch merely looks like it is hanging.
#
# Hence: cap `apt-get update` by wall clock, judge success from the output
# rather than the exit status, and on failure retry the canonical archive --
# which is CDN-backed and is the same last-resort mirror cloud-init itself falls
# back to when no mirror can be resolved. If that also fails, fail loudly.
#
# The fallback deliberately builds a *self-contained* apt configuration rather
# than trying to subtract the bad mirror from the node's own. Overriding only
# Dir::Etc::sourcelist would miss deb822 images (Ubuntu 24.04+ describes the
# distro archive in /etc/apt/sources.list.d/ubuntu.sources, not in
# sources.list), so the unreachable mirror would still be consulted. Pointing
# both sourcelist and sourceparts at our own directory, seeded with the
# canonical archive plus a copy of SkyPilot's own repo lists, covers both
# layouts -- and means a transient error from an unrelated third-party repo on
# the node results in one fallback attempt rather than an aborted launch. The
# node's real apt configuration is never modified.
_APT_HELPERS = textwrap.dedent("""\
    sky_apt_dir=""
    sky_apt_opts="%(opts)s"
    sky_apt_own_lists="/etc/apt/sources.list.d/fluent-bit.list"
    sky_apt_os_id=$(grep -oP '(?<=^ID=).*' /etc/os-release 2>/dev/null || \
        lsb_release -is 2>/dev/null | tr '[:upper:]' '[:lower:]')
    sky_apt_codename=$(grep -oP '(?<=VERSION_CODENAME=).*' /etc/os-release \
        2>/dev/null || lsb_release -cs 2>/dev/null)
    sky_apt_use_fallback() {
      # Already switched once; there is nothing further to fall back to.
      [ -n "$sky_apt_dir" ] && return 1
      # Only Ubuntu pins a per-region mirror that can vanish wholesale. Debian's
      # default (deb.debian.org) is already CDN-backed, so leave it alone.
      [ "$sky_apt_os_id" = ubuntu ] || return 1
      [ -n "$sky_apt_codename" ] || return 1
      sudo mkdir -p %(dir)s/sources.list.d || return 1
      printf 'deb http://archive.ubuntu.com/ubuntu %%s main universe\n'\
'deb http://security.ubuntu.com/ubuntu %%s-security main universe\n' \
        "$sky_apt_codename" "$sky_apt_codename" \
        | sudo tee %(dir)s/archive.list >/dev/null || return 1
      sky_apt_dir=%(dir)s
      echo "SkyPilot: distro package mirror unreachable; retrying the logging" \
        "agent install via archive.ubuntu.com" >&2
      return 0
    }
    sky_apt_sync_own_lists() {
      # Carry SkyPilot's own repo lists into the fallback config, re-synced on
      # every call because they may be added after the fallback is set up. Only
      # ours: copying all of sources.list.d would drag the unreachable distro
      # mirror back in on deb822 images.
      for _f in $sky_apt_own_lists; do
        if [ -f "$_f" ]; then
          sudo cp "$_f" "$sky_apt_dir/sources.list.d/" 2>/dev/null || true
        fi
      done
    }
    sky_apt_exec() {
      # $1: wall-clock cap, empty for none. Remaining args go to apt-get.
      _t=$1; shift
      _srcs=""
      if [ -n "$sky_apt_dir" ]; then
        sky_apt_sync_own_lists
        _srcs="-o Dir::Etc::sourcelist=$sky_apt_dir/archive.list"
        _srcs="$_srcs -o Dir::Etc::sourceparts=$sky_apt_dir/sources.list.d"
      fi
      # LC_ALL=C: the success check greps apt's messages, which are localised.
      # Without this a fully failed update on a non-English node would look
      # like a success, since apt-get update exits 0 either way.
      if [ -n "$_t" ]; then
        timeout "$_t" sudo env LC_ALL=C apt-get $sky_apt_opts $_srcs "$@" 2>&1
      else
        sudo env LC_ALL=C apt-get $sky_apt_opts $_srcs "$@" 2>&1
      fi
    }
    sky_apt_run() {
      _timeout=$1; shift
      set +e
      while :; do
        _out=$(sky_apt_exec "$_timeout" "$@")
        _rc=$?
        printf '%%s\n' "$_out"
        if [ "$_rc" -eq 0 ] && \
            ! printf '%%s' "$_out" | grep -qE 'Failed to fetch|^Err:'; then
          set -e
          return 0
        fi
        if ! sky_apt_use_fallback; then
          echo "SkyPilot: 'apt-get $*' failed (rc=$_rc) and no reachable" \
            "package mirror is left to try" >&2
          set -e
          return 1
        fi
        # /var/lib/apt/lists holds nothing for the sources just selected, so
        # anything other than `update` would resolve against an empty package
        # universe and fail with a misleading 'Unable to locate package'.
        # `update` needs no such priming: its own retry fetches the indexes.
        if [ "$1" != update ]; then
          printf '%%s\n' "$(sky_apt_exec %(update_timeout)s update)"
        fi
      done
    }
    sky_apt_update() { sky_apt_run %(update_timeout)s update; }
    sky_apt_install() { sky_apt_run "" install -y "$@"; }
    """) % {
    'opts': _APT_OPTS,
    'dir': _APT_FALLBACK_DIR,
    'update_timeout': _APT_UPDATE_TIMEOUT,
}


class LoggingAgent(abc.ABC):
    """Base class for all logging agents.

    Each agent should implement the `get_setup_command` and
    `get_credential_file_mounts` methods to return the setup command and
    credential file mounts for the agent for provisioner to setup the agent
    on each node.
    """

    @abc.abstractmethod
    def get_setup_command(self,
                          cluster_name: resources_utils.ClusterName) -> str:
        pass

    @abc.abstractmethod
    def get_credential_file_mounts(self) -> Dict[str, str]:
        pass


class FluentbitAgent(LoggingAgent):
    """Base class for logging store that use fluentbit as the agent."""

    def get_setup_command(self,
                          cluster_name: resources_utils.ClusterName) -> str:
        install_cmd = (
            # pylint: disable=line-too-long
            'if ! command -v fluent-bit >/dev/null 2>&1 && [ ! -f /opt/fluent-bit/bin/fluent-bit ]; then\n'
            f'{_APT_HELPERS}'
            'sky_apt_update\n'
            'sky_apt_install gnupg\n'
            # pylint: disable=line-too-long
            'sudo sh -c \'curl -L https://packages.fluentbit.io/fluentbit.key | gpg --dearmor > /usr/share/keyrings/fluentbit-keyring.gpg\'\n'
            # pylint: disable=line-too-long
            'echo "deb [signed-by=/usr/share/keyrings/fluentbit-keyring.gpg] https://packages.fluentbit.io/$sky_apt_os_id/$sky_apt_codename $sky_apt_codename main" | sudo tee /etc/apt/sources.list.d/fluent-bit.list\n'
            'sky_apt_update\n'
            # Pin to <5.0 because fluent-bit 5.0.0 broke the stackdriver
            # output plugin's service account auth (google_service_credentials
            # and GOOGLE_APPLICATION_CREDENTIALS are both ignored).
            'sky_apt_install "fluent-bit=4.*"\n'
            'fi')
        cfg = self.fluentbit_config(cluster_name)
        cfg_path = os.path.join(constants.LOGGING_CONFIG_DIR, 'fluentbit.yaml')
        config_cmd = (
            f'mkdir -p {constants.LOGGING_CONFIG_DIR} && '
            f'echo {shlex.quote(cfg)} > {cfg_path} && '
            # Resolve ~ to $HOME since fluent-bit does not
            # expand ~ in file paths or config values.
            f'sed -i "s|~/|$HOME/|g" {cfg_path}')
        kill_prior_cmd = (
            'if [ -f "/tmp/fluentbit.pid" ]; then '
            # pylint: disable=line-too-long
            'echo "Killing prior fluent-bit process $(cat /tmp/fluentbit.pid)"; '
            'kill "$(cat /tmp/fluentbit.pid)" || true; '
            'fi')
        start_cmd = ('nohup $(command -v fluent-bit || '
                     'echo "/opt/fluent-bit/bin/fluent-bit") '
                     f'-c {cfg_path} > /tmp/fluentbit.log 2>&1 & '
                     'echo $! > /tmp/fluentbit.pid')
        return ('set -e; '
                f'{install_cmd}; '
                f'{config_cmd}; '
                f'{kill_prior_cmd}; '
                f'{start_cmd}')

    def fluentbit_config(self,
                         cluster_name: resources_utils.ClusterName) -> str:
        cfg_dict = {
            'parsers': [{
                'name': 'sky-ray-parser',
                'format': 'regex',
                # pylint: disable=line-too-long
                'regex': r'(?:\x1b\[[\d;]+m)?\((?<worker_name>[^,]+)(?:,\s*rank=(?<rank>\d+))?(?:,\s*pid=(?<pid>\d+))(?:,\s*ip=(?<ip>[\d.]+))?\)(?:\x1b\[[\d;]+m)?\s*(?<log_line>.*)',
                'types': 'rank:integer pid:integer',
            }],
            'pipeline': {
                'inputs': [{
                    'name': 'tail',
                    'path': f'{constants.SKY_LOGS_DIRECTORY}/*/*.log',
                    'path_key': 'log_path',
                    # Shorten the refresh interval from 60s to 1s since every
                    # job creates a new log file and we must be responsive
                    # for this: the VM might be autodown within a minute
                    # right after the job completion.
                    'refresh_interval': 1,
                }],
                'filters': [{
                    'name': 'parser',
                    'match': '*',
                    'key_name': 'log',
                    'parser': 'sky-ray-parser',
                    'preserve_key': 'on',  # preserve field for backwards compat
                    'reserve_data': 'on',
                }],
                'outputs': [self.fluentbit_output_config(cluster_name)],
            }
        }
        return yaml_utils.dump_yaml_str(cfg_dict)

    @abc.abstractmethod
    def fluentbit_output_config(
            self, cluster_name: resources_utils.ClusterName) -> Dict[str, Any]:
        pass
