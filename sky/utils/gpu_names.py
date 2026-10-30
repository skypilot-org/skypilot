"""Canonical GPU names shared across backends (Kubernetes, Slurm, etc.)."""

import re
from typing import List, Optional

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


# Vendor prefixes stripped during normalization for matching purposes.
_GPU_VENDOR_PREFIXES = ('nvidia', 'amd', 'intel', 'tesla')


def normalize_gpu_name(name: str) -> str:
    """Normalize a GPU name for fuzzy comparison.

    Strips vendor prefixes, normalizes separators, and lowercases. Used only
    for matching, never for submission.

    Examples:
        'nvidia_h100_80gb_hbm3' -> 'h100-80gb-hbm3'
        'H100'                -> 'h100'
        'A100-SXM-80GB'       -> 'a100-sxm-80gb'
    """
    result = name.lower().replace('_', '-')
    for prefix in _GPU_VENDOR_PREFIXES:
        if result.startswith(prefix + '-'):
            result = result[len(prefix) + 1:]
            break
    return result


def is_segment_subsequence(segments_a: List[str],
                           segments_b: List[str]) -> bool:
    """Check if segments_a appears as an ordered subsequence of segments_b.

    Each segment must match exactly (preventing e.g. 'l4' matching 'l40').

    Examples:
        (['h100'], ['h100', '80gb', 's'])          -> True
        (['a100', '80gb'], ['a100', 'sxm4', '80gb']) -> True
        (['v100', '32gb'], ['v100', 'pcie', '16gb']) -> False
        (['l4'], ['l40'])                           -> False
    """
    # The iterator is stateful: once an element is consumed it won't be
    # revisited, so matches are always found in left-to-right order.
    b_iter = iter(segments_b)
    for seg in segments_a:
        for b_seg in b_iter:
            if seg == b_seg:
                break
        else:
            return False
    return True


def accelerator_name_matches(requested_acc: str, candidate_raw: str) -> bool:
    """Check if a requested accelerator name matches a scheduler's raw type.

    Matching rules (checked in order):
    1. Case-insensitive exact match of raw strings.
    2. Normalized forms are equal (vendor-prefix stripped, separators unified).
    3. Segment subsequence: the shorter name's dash-segments appear in order
       within the longer name's segments. Handles both prefix cases
       (H100 ~ H100-80GB-S) and non-contiguous memory variants
       (A100-80GB ~ A100-SXM4-80GB, V100-32GB ~ V100-PCIE-32GB).
       Exact segment matching prevents false positives (L4 != L40).

    Args:
        requested_acc: The accelerator name requested by the user
            (e.g. 'H100', 'A100-80GB').
        candidate_raw: A raw GPU type string from the scheduler's node
            metadata (e.g. 'NVIDIA_H100_80GB_HBM3').
    """
    if requested_acc.lower() == candidate_raw.lower():
        return True
    req_norm = normalize_gpu_name(requested_acc)
    cand_norm = normalize_gpu_name(candidate_raw)
    if req_norm == cand_norm:
        return True
    req_segments = req_norm.split('-')
    cand_segments = cand_norm.split('-')
    if len(req_segments) < len(cand_segments):
        return is_segment_subsequence(req_segments, cand_segments)
    if len(cand_segments) < len(req_segments):
        return is_segment_subsequence(cand_segments, req_segments)
    return False


def canonicalize_raw_gpu_name(raw_name: str) -> str:
    """Convert a scheduler's raw GPU type to a canonical display name.

    Iterates CANONICAL_GPU_NAMES (most-specific first) and returns the
    first canonical name whose normalized form matches the raw string.
    Falls back to uppercasing.

    Examples:
        'nvidia_h100_80gb_hbm3'  -> 'H100-80GB'
        'nvidia_a100_sxm4_80gb'  -> 'A100-80GB'
        'nvidia_l40s'            -> 'L40S'
        'H100'                   -> 'H100'
        'unknown_custom_gpu'     -> 'UNKNOWN_CUSTOM_GPU'
    """
    raw_norm = normalize_gpu_name(raw_name)
    raw_segments = raw_norm.split('-')
    for canonical in CANONICAL_GPU_NAMES:
        can_norm = normalize_gpu_name(canonical)
        if can_norm == raw_norm:
            return canonical
        # One-directional (canonical into raw) because the list is ordered
        # most-specific first: a reverse match would let 'H100' resolve to
        # 'H100-80GB'.
        can_segments = can_norm.split('-')
        if len(can_segments) < len(raw_segments):
            if is_segment_subsequence(can_segments, raw_segments):
                return canonical
    return raw_name.upper()
