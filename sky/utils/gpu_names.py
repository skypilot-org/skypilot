"""Canonical GPU names shared across backends (Kubernetes, Slurm, etc.)."""

import re
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

# Single-device memory (GiB) for bare GPU names that have a memory-variant
# sibling (e.g. 'A100' 40GB vs 'A100-80GB' 80GB). These bare names carry no
# memory suffix to parse, so their memory must be stated explicitly. Used to
# distinguish a real memory variant (different hardware) from a same-hardware
# rename (e.g. 'H100' == 'H100-80GB', equal memory) during prefix-based name
# matching, so that an 'A100-80GB' request is not silently scheduled onto a
# 40GB A100 node.
#
# Conservative allowlist: only bare names whose memory differs from a suffixed
# sibling need an entry. Names with an explicit memory suffix (e.g. 'A100-80GB',
# 'A100-80G', 'V100-32GB') are handled by parsing the suffix, so they need no
# entry here.
CANONICAL_GPU_MEMORY_GIB = {
    'A100': 40,
    'V100': 16,
}
_CANONICAL_GPU_MEMORY_GIB_LOWER = {
    k.lower(): v for k, v in CANONICAL_GPU_MEMORY_GIB.items()
}

# Matches a trailing memory suffix like '-80GB', '-80G', or '-32gb' and
# captures the size in GiB. Tolerates the common typo of dropping the 'B'
# (e.g. 'A100-80G') since user-typed names are not always canonicalized.
_MEMORY_SUFFIX_RE = re.compile(r'-(\d+)\s*gb?(?:-|$)', re.IGNORECASE)

# Suffix tokens that describe how a GPU is packaged rather than which GPU it
# is: a bus/form factor or an on-device memory size. Two names differing only
# by these tokens denote the same physical GPU (e.g. 'H200-SXM-80GB' is an
# H200). Any other token marks a distinct model -- 'H100-MEGA', 'H100-NVL' and
# 'RTX3090-TI' are all different GPUs from their bare-name prefix, and several
# appear in CANONICAL_GPU_NAMES in their own right.
_FORM_FACTOR_TOKENS = frozenset({
    'sxm',
    'sxm2',
    'sxm3',
    'sxm4',
    'sxm5',
    'pci',
    'pcie',
    'hbm',
    'hbm2',
    'hbm2e',
    'hbm3',
    'hbm3e',
})

# Matches a standalone memory token like '80GB', '80G' or '32gb'.
_MEMORY_TOKEN_RE = re.compile(r'^\d+\s*gb?$', re.IGNORECASE)


def is_packaging_variant(shorter: str, longer: str) -> bool:
    """Returns whether `longer` names the same physical GPU as `shorter`.

    `longer` qualifies only when it is `shorter` followed by '-' and suffix
    tokens that all describe packaging -- a form factor ('SXM', 'PCIE') or a
    device memory size ('80GB'). This distinguishes a fallback name for the
    same hardware, e.g. 'H200-SXM-80GB' for 'H200', from a genuinely different
    model that happens to share a prefix, e.g. 'H100-MEGA' for 'H100'.

    Comparison is case-insensitive. Callers still need to compare device memory
    (see get_gpu_device_memory_gib) to reject a smaller-memory node.
    """
    shorter_lower = shorter.lower()
    longer_lower = longer.lower()
    if not longer_lower.startswith(f'{shorter_lower}-'):
        return False
    suffix_tokens = longer_lower[len(shorter_lower) + 1:].split('-')
    return all(token in _FORM_FACTOR_TOKENS or
               _MEMORY_TOKEN_RE.match(token) is not None
               for token in suffix_tokens)


def get_gpu_device_memory_gib(name: str) -> Optional[int]:
    """Returns the single-device memory (GiB) implied by a GPU name.

    Resolution is intentionally scoped to the GPU families listed in
    CANONICAL_GPU_MEMORY_GIB, so this never changes matching for GPUs we don't
    explicitly track:
    1. An exact bare-name match (e.g. 'A100' -> 40) returns the mapped value.
    2. A memory suffix (e.g. 'A100-80GB', or the typo 'A100-80G') is parsed
       only when the part before the suffix is itself a tracked bare name
       (e.g. 'a100'). This handles names that were not canonicalized on
       Kubernetes-only clusters without widening the blast radius.

    Returns None otherwise, in which case callers should fall back to their
    default matching behavior. Lookup is case-insensitive.
    """
    lower = name.lower()
    if lower in _CANONICAL_GPU_MEMORY_GIB_LOWER:
        return _CANONICAL_GPU_MEMORY_GIB_LOWER[lower]
    match = _MEMORY_SUFFIX_RE.search(lower)
    if match is not None:
        base = lower[:match.start()]
        if base in _CANONICAL_GPU_MEMORY_GIB_LOWER:
            return int(match.group(1))
    return None
