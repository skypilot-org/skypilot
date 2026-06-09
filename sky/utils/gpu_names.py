"""Canonical GPU names shared across backends (Kubernetes, Slurm, etc.)."""

from typing import Optional

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

# Single-device memory (GiB) for canonical GPU names that participate in
# memory-variant prefix relationships (e.g. 'A100' 40GB vs 'A100-80GB' 80GB).
# Used to distinguish a real memory variant (different hardware) from a
# same-hardware rename (e.g. 'H100' == 'H100-80GB', equal memory) during
# prefix-based name matching, so that an 'A100-80GB' request is not silently
# scheduled onto a 40GB A100 node.
#
# This is a conservative allowlist: only names listed here are disambiguated.
# Any other pair keeps the legacy prefix-match behavior. Add an entry only
# when a canonical name has a sibling that differs solely by a memory suffix.
CANONICAL_GPU_MEMORY_GIB = {
    'A100': 40,
    'A100-80GB': 80,
    'V100': 16,
    'V100-32GB': 32,
}
_CANONICAL_GPU_MEMORY_GIB_LOWER = {
    k.lower(): v for k, v in CANONICAL_GPU_MEMORY_GIB.items()
}


def get_canonical_gpu_memory_gib(name: str) -> Optional[int]:
    """Returns the single-device memory (GiB) for a canonical GPU name.

    Lookup is case-insensitive. Returns None if the name is not a known
    memory-variant canonical name (in which case callers should fall back to
    their default matching behavior).
    """
    return _CANONICAL_GPU_MEMORY_GIB_LOWER.get(name.lower())
