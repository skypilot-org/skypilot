"""AppArmor fix for containerd on SSH Node Pool nodes.

containerd's default AppArmor profile (before containerd/containerd#14487)
only lets processes labeled exactly `cri-containerd.apparmor.d` signal each
other. With kernel.apparmor_restrict_unprivileged_unconfined=1 (the default on
Ubuntu 26.04), runc's switch to that profile becomes a stack, container
processes are labeled `cri-containerd.apparmor.d//&unconfined`, and every
in-container kill() fails with EACCES. SkyPilot pods then hang on their first
file sync and cannot cancel jobs. See skypilot-org/skypilot#10167.

The k3s that `sky ssh up` installs embeds a containerd without the fix. We
load the fixed profile under the same name ourselves. containerd only loads
its own profile when none of that name is loaded yet, so ours is used: right
away (replacing a loaded profile also applies to running containers), and
after a reboot, since apparmor.service loads /etc/apparmor.d before k3s
starts.

TODO(zpoint): Pin k3s to a release whose containerd includes
containerd/containerd#14487 and stop installing this profile. A profile we
installed would otherwise keep shadowing containerd's own, including later
changes to it.
"""

PROFILE_NAME = 'cri-containerd.apparmor.d'
PROFILE_PATH = f'/etc/apparmor.d/{PROFILE_NAME}'

# containerd's default profile template (contrib/apparmor/template.go on
# release/2.3, which includes the fix) as rendered on Ubuntu: abi/3.0,
# tunables/global and abstractions/base all exist, the k3s daemon runs
# unconfined, and SSH Node Pools do not use RootlessKit. Keep in sync with
# that template; the only change from the profile k3s loads today is the two
# `//&unconfined` peer rules.
PROFILE = f"""\
abi <abi/3.0>,
#include <tunables/global>

profile {PROFILE_NAME} flags=(attach_disconnected,mediate_deleted) {{
  #include <abstractions/base>

  network,
  capability,
  file,
  umount,
  # Host (privileged) processes may send signals to container processes.
  signal (receive) peer=unconfined,
  # runc may send signals to container processes.
  signal (receive) peer=runc,
  # crun may send signals to container processes.
  signal (receive) peer=crun,
  # Container processes may send signals amongst themselves.
  signal (send,receive) peer={PROFILE_NAME},
  # With kernel.apparmor_restrict_unprivileged_unconfined=1, an unprivileged
  # profile change is converted to stacking, producing
  # "{PROFILE_NAME}//&unconfined". Allow signals to peers with this stacked
  # label as well.
  signal (send,receive) peer={PROFILE_NAME}//&unconfined,

  deny @{{PROC}}/* w,   # deny write for all files directly in /proc (not in a subdir)
  # deny write to files not in /proc/<number>/** or /proc/sys/**
  deny @{{PROC}}/{{[^1-9/],[^1-9/][^0-9/],[^1-9s/][^0-9y/][^0-9s/],[^1-9/][^0-9/][^0-9/][^0-9/]*}}/** w,
  deny @{{PROC}}/sys/[^k]** w,  # deny /proc/sys except /proc/sys/k* (effectively /proc/sys/kernel)
  deny @{{PROC}}/sys/kernel/{{?,??,[^s][^h][^m]**}} w,  # deny everything except shm* in /proc/sys/kernel/
  deny @{{PROC}}/sysrq-trigger rwklx,
  deny @{{PROC}}/kcore rwklx,

  deny mount,

  deny /sys/[^f]*/** wklx,
  deny /sys/f[^s]*/** wklx,
  deny /sys/fs/[^c]*/** wklx,
  deny /sys/fs/c[^g]*/** wklx,
  deny /sys/fs/cg[^r]*/** wklx,
  deny /sys/firmware/** rwklx,
  deny /sys/devices/virtual/powercap/** rwklx,
  deny /sys/kernel/security/** rwklx,

  # allow processes within the container to trace each other,
  # provided all other LSM and yama setting allow it.
  ptrace (trace,tracedby,read,readby) peer={PROFILE_NAME},
  # Same stacked-label peer as the signal rule above.
  ptrace (trace,tracedby,read,readby) peer={PROFILE_NAME}//&unconfined,
}}
"""

# Printed by install_profile_cmd when it installed the profile.
INSTALLED_MARKER = 'SKYPILOT_APPARMOR_PROFILE_INSTALLED'


def install_profile_cmd(askpass_block: str) -> str:
    """Shell command that installs the fixed profile where it is needed.

    A no-op unless the node stacks container profiles, i.e. AppArmor is
    enabled and kernel.apparmor_restrict_unprivileged_unconfined is 1, so
    nodes that are not affected are left alone.
    """
    return f"""
        {askpass_block}
        if [ "$(cat /proc/sys/kernel/apparmor_restrict_unprivileged_unconfined 2>/dev/null)" = "1" ] &&
           [ -d /sys/kernel/security/apparmor ] &&
           command -v apparmor_parser >/dev/null 2>&1; then
            sudo -A tee {PROFILE_PATH} >/dev/null <<'SKYPILOT_APPARMOR_EOF'
{PROFILE}SKYPILOT_APPARMOR_EOF
            sudo -A apparmor_parser -r {PROFILE_PATH} &&
            echo {INSTALLED_MARKER}
        fi
    """
