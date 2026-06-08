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

# Canonical GPU names that refer to the *same hardware* under different
# canonical names, i.e. a node label that used to canonicalize to one name now
# canonicalizes to the other (a historical rename). These are distinct entries
# in CANONICAL_GPU_NAMES but must be treated as equivalent when matching a
# requested accelerator against a node's GPU label.
#
# This is intentionally an explicit allow-list, NOT a structural/lexical rule.
# 'H100' and 'H100-80GB' are the same hardware (a bare 'H100' is already 80GB),
# but 'A100' (40GB) and 'A100-80GB' (80GB) -- which are structurally identical
# pairs -- are *different* hardware and must never match. A purely lexical rule
# (e.g. '-'-prefix matching) cannot tell these apart, so the equivalence is
# enumerated here.
_CANONICAL_GPU_NAME_ALIASES = {
    'h100': {'h100-80gb'},
    'h100-80gb': {'h100'},
}


def are_canonical_aliases(name_a: str, name_b: str) -> bool:
    """Returns whether two canonical GPU names refer to the same hardware.

    Used to keep clusters working across GPU label canonicalization renames
    (e.g. a label that used to map to 'H100' now maps to 'H100-80GB') while
    still treating genuinely different hardware (e.g. 'A100' (40GB) vs
    'A100-80GB' (80GB)) as distinct. Matching is case-insensitive.
    """
    return name_b.lower() in _CANONICAL_GPU_NAME_ALIASES.get(
        name_a.lower(), frozenset())
