"""Canonical GPU names shared across backends (Kubernetes, Slurm, etc.)."""

# Canonical GPU names for GPU detection and labeling.
#
# IMPORTANT: Order matters — more-specific names must come before
# less-specific ones (e.g. 'L40S' before 'L40' before 'L4') so that
# prefix/substring matching picks the most precise canonical name first.
CANONICAL_GPU_NAMES = [
    # Blackwell architecture (2024+)
    'GB300',
    'GB200',
    'B300',
    'B200',
    'B100',
    # Hopper architecture
    'GH200',
    'H200',
    'H100-80GB',
    'H100-MEGA',
    'H100',
    # Ampere architecture
    'A100-80GB',
    'A100',
    'A10G',
    'A10',
    'A16',
    'A30',
    'A40',
    # Ada Lovelace architecture - Professional (RTX Ada)
    'RTX6000-Ada',
    'L40S',
    'L40',
    'L4',
    # Quadro/RTX Professional (Ampere)
    'A6000',
    'A5000',
    'A4000',
    # Older architectures - Volta/Pascal/Turing
    'V100-32GB',
    'V100',
    'P100',
    'P40',
    'P4000',
    'P4',
    'T4g',
    'T4',
    'K80',
    'M60',
]
