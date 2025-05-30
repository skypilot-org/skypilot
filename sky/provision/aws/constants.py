"""AWS provisioning constants."""
import textwrap

# AWS instance types that support EFA networking
# Based on the table in the EFA README
EFA_SUPPORTED_INSTANCE_TYPES = [
    'p4d.24xlarge',
    'p4de.24xlarge',
    'p5.48xlarge',
    'p5e.48xlarge',
    'p5en.48xlarge',
    'g5.8xlarge',
    'g5.12xlarge',
    'g5.16xlarge',
    'g5.24xlarge',
    'g5.48xlarge',
    'g4dn.8xlarge',
    'g4dn.12xlarge',
    'g4dn.16xlarge',
    'g4dn.metal',
    'g6.8xlarge',
    'g6.12xlarge',
    'g6.16xlarge',
    'g6.24xlarge',
    'g6.48xlarge',
    'g6e.8xlarge',
    'g6e.12xlarge',
    'g6e.16xlarge',
    'g6e.24xlarge',
    'g6e.48xlarge',
]

# Mapping of instance types to number of EFA devices
# Based on the table in the EFA README
EFA_DEVICE_COUNT = {
    'p4d.24xlarge': 4,
    'p4de.24xlarge': 4,
    'p5.48xlarge': 32,
    'p5e.48xlarge': 32,
    'p5en.48xlarge': 16,
    'g5.8xlarge': 1,
    'g5.12xlarge': 1,
    'g5.16xlarge': 1,
    'g5.24xlarge': 1,
    'g5.48xlarge': 1,
    'g4dn.8xlarge': 1,
    'g4dn.12xlarge': 1,
    'g4dn.16xlarge': 1,
    'g4dn.metal': 1,
    'g6.8xlarge': 1,
    'g6.12xlarge': 1,
    'g6.16xlarge': 1,
    'g6.24xlarge': 1,
    'g6.48xlarge': 1,
    'g6e.8xlarge': 1,
    'g6e.12xlarge': 1,
    'g6e.16xlarge': 1,
    'g6e.24xlarge': 2,
    'g6e.48xlarge': 4,
}

# User data script for setting up EFA networking
# This will configure the necessary environment variables for NCCL and EFA
EFA_USER_DATA = textwrap.dedent("""
        #cloud-config
        users:
          - name: skypilot:ssh_user
            shell: /bin/bash
            sudo: ALL=(ALL) NOPASSWD:ALL
            ssh_authorized_keys:
              - |-
                skypilot:ssh_public_key_content
        write_files:
          - path: /etc/apt/apt.conf.d/20auto-upgrades
            content: |
              APT::Periodic::Update-Package-Lists "0";
              APT::Periodic::Download-Upgradeable-Packages "0";
              APT::Periodic::AutocleanInterval "0";
              APT::Periodic::Unattended-Upgrade "0";
          - path: /etc/apt/apt.conf.d/10cloudinit-disable
            content: |
              APT::Periodic::Enable "0";
          - path: /etc/apt/apt.conf.d/52unattended-upgrades-local
            content: |
              Unattended-Upgrade::DevRelease "false";
              Unattended-Upgrade::Allowed-Origins {};
          - path: /etc/profile.d/efa-env.sh
            content: |
              # EFA environment variables for NCCL
              export PATH=$PATH:/opt/amazon/efa/bin
              export LD_LIBRARY_PATH=/opt/amazon/openmpi/lib:/opt/nccl/build/lib:/opt/amazon/efa/lib:/opt/aws-ofi-nccl/install/lib:/usr/local/nvidia/lib:$LD_LIBRARY_PATH
              export NCCL_DEBUG=INFO
              export NCCL_BUFFSIZE=8388608
              export NCCL_P2P_NET_CHUNKSIZE=524288
              export FI_PROVIDER="efa"
              export FI_EFA_USE_DEVICE_RDMA=1
              export FI_EFA_FORK_SAFE=1
              export NCCL_ALGO=Ring
              export NCCL_PROTO=Simple
        bootcmd:
          - systemctl stop apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service
          - systemctl disable apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service
          - systemctl mask apt-daily.service apt-daily-upgrade.service unattended-upgrades.service
          - systemctl daemon-reload
        runcmd:
          - echo "Setting up EFA environment variables..."
          - source /etc/profile.d/efa-env.sh
          - echo "EFA environment setup complete"
    """).strip()

# Docker run options for EFA networking
# These options expose EFA devices and set up the networking environment
EFA_DOCKER_OPTIONS = [
    '--cap-add=IPC_LOCK',
    '--cap-add=SYS_PTRACE',
    '--shm-size=1g',
    '--ulimit memlock=-1',
    '--ulimit stack=67108864',
    '--device /dev/infiniband/uverbs0:/dev/infiniband/uverbs0',
    '--device /dev/infiniband/uverbs1:/dev/infiniband/uverbs1',
    '--device /dev/infiniband/uverbs2:/dev/infiniband/uverbs2',
    '--device /dev/infiniband/uverbs3:/dev/infiniband/uverbs3',
    '--env FI_PROVIDER=efa',
    '--env FI_EFA_USE_DEVICE_RDMA=1',
    '--env FI_EFA_FORK_SAFE=1',
    '--env NCCL_DEBUG=INFO',
    '--env NCCL_BUFFSIZE=8388608',
    '--env NCCL_P2P_NET_CHUNKSIZE=524288',
    '--env NCCL_ALGO=Ring',
    '--env NCCL_PROTO=Simple',
]
