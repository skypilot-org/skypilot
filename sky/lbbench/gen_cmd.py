"""Utils for generating benchmark commands."""
import argparse
import collections
import json
import os
from pathlib import Path
import shlex
import tempfile
from typing import List, Optional # Added Optional

from sky.lbbench import utils

raw_describes = [
    'sgl',
    'sky_sgl_enhanced',
    'sky_vanilla_least_load',
    'sky_least_load_enhanced',
    'sky_consistent_hashing',
    'sky_consistent_hashing_enhanced',
    'sky_round_robin',
    'sky_round_robin_enhanced',
    # 'sky_consistent_hashing_irregular_user',
    # 'sky_consistent_hashing_enhanced_irregular_user',
    # 'sky_consistent_hashing_prefix_hash',
    # 'sky_consistent_hashing_enhanced_prefix_hash',
    # 'sky_consistent_hashing_real_uid',
    # 'sky_consistent_hashing_enhanced_real_uid',
    'sky_pull_pull',
    # 'sky_pull_pull_small',
    'sky_pull_pull_small_3',
    'sky_push_pull',
    'sky_push_push',
    # 'sky_pull_pull_rate_limit_prefix_tree',
    'sky_walk_prefix',
    'sky_walk_ll',
    'sky_walk_rr',
    'sky_walk_ch',
    'gke_gateway', # Added GKE
]
raw_presents = [
    'SGL',
    'SGL+SelPush',
    'LL',
    'LL+SelPush',
    # 'ConsistentHashing',
    # 'ConsistentHashing+SelPush',
    # 'ConsistentHashing/IrregularUser',
    # 'ConsistentHashing+SelPush/IrregularUser',
    # 'CHash/PrefixHash',
    # 'CHash+SelPush/PrefixHash',
    'CH',
    'CH+SelPush',
    'RR',
    'RR+SelPush',
    'Ours\\n[Pull/Steal+Pull]',
    # 'Ours\\n[Pull/StealSmall+Pull]',
    'Ours\\n[Pull/StealSmall3+Pull]',
    'Ours\\n[Push+Pull]',
    'Ours\\n[Push+Push]',
    # 'Ours\\n[SelPush/Prefix+Pull]',
    'SkyWalk/Prefix',
    'SkyWalk/LL',
    'SkyWalk/RR',
    'SkyWalk/CH',
    'GKE Gateway', # Added GKE
]

# This list now includes all possible systems, including GKE
all_systems = [
    0,  # sgl router
    1,  # sgl router enhanced
    2,  # vanilla least load
    3,  # global least load
    4,  # consistent hashing
    5,  # consistent hashing with selective pushing
    6,  # round robin
    7,  # rr with selective pushing
    8,  # sky pulling in lb, pulling in replica, but workload stealing
    9,  # sky pulling in lb, pulling in replica, but steal small #requests
    10, # sky pushing in lb, pulling in replica
    11, # sky pushing in lb, pushing in replica
    12, # selective pushing for both lb and replica. with prefix tree.
    13, # selective pushing for both lb and replica. with least load.
    14, # selective pushing for both lb and replica. with round robin.
    15, # selective pushing for both lb and replica. with consistent hashing.
    16, # gke_gateway
]

# Default enabled systems (filtered list), keeping original main behavior unless overridden by --run-systems
_default_enabled_systems_filter = [6, 7, 15]
# enabled_systems = [15] # This was a commented out alternative in main

# These will be populated in main() based on args.run_systems or the default filter
enabled_systems: List[int] = []
describes: List[str] = []
presents: List[str] = []


ct = None
sn2st = None


def _get_head_ip_for_cluster(cluster: str) -> str:
    global ct
    if ct is None:
        ct = utils.sky_status()
    for c in ct:
        if c['name'] == cluster:
            return c['handle'].head_ip
    raise ValueError(f'Cluster {cluster} not found')


def _get_endpoint_for_traffic(index: int, sns_item: Optional[str], gke_endpoint: Optional[str] = None) -> str:
    # sns_item is the specific service name for this index, or None
    # GKE Gateway is index 16 in all_systems
    if index == 16:  # gke_gateway
        if gke_endpoint:
            if not gke_endpoint.startswith(('http://', 'https://')):
                return f'http://{gke_endpoint}'
            return gke_endpoint
        return 'http://34.117.239.237:80'  # Default GKE endpoint

    if index < len(utils.single_lb_clusters): # Handles SGL (0), SGL_Enhanced (1)
        cluster = utils.single_lb_clusters[index]
        cluster_ip = _get_head_ip_for_cluster(cluster)
        port = 9001 if index == 0 else 9002
        return f'{cluster_ip}:{port}'

    # SkyPilot services (indices 2-15 in all_systems)
    global sn2st
    if sn2st is None:
        sn2st = {s['name']: s for s in utils.sky_serve_status()}
    
    if sns_item is None:
        raise ValueError(f'Service name (sns_item) is None for a SkyPilot service index {index}. This should not happen if service name is required.')

    if sns_item not in sn2st:
        raise ValueError(f'Service {sns_item} for index {index} not found in sky serve status.')
    return sn2st[sns_item]['endpoint']


def _region_cluster_name(r: str) -> str:
    return f'llmc-{r}'


def main():
    global enabled_systems, describes, presents # Allow modification of these globals

    parser = argparse.ArgumentParser()
    # Changed nargs to '*' and required to False, added default=[] for --service-names
    parser.add_argument('--service-names', type=str, nargs='*', default=[], help='Service names for SkyPilot services')
    parser.add_argument('--exp-name', type=str, required=True)
    parser.add_argument('--extra-args', type=str, default='')
    parser.add_argument('--output-dir', type=str, default='@temp')
    parser.add_argument('--regions', type=str, default=None, nargs='+')
    parser.add_argument('--region-to-args', type=str, default=None)
    parser.add_argument('--reload-client', action='store_true')
    # Added GKE arguments
    parser.add_argument('--gke-endpoint', type=str, default='34.117.239.237:80', help='GKE Gateway endpoint (IP:port)')
    parser.add_argument('--run-systems', type=int, nargs='+', default=None, help='Indices of systems to run (e.g., 0 1 16). Default uses hardcoded filter [6,7,15].')
    
    args = parser.parse_args()

    if args.run_systems is None:
        enabled_systems = [idx for idx in _default_enabled_systems_filter if idx in all_systems]
    else:
        enabled_systems = [idx for idx in args.run_systems if idx in all_systems]
    
    if not enabled_systems:
        raise ValueError("No valid systems selected to run. Check --run-systems values or the default filter.")

    describes = [raw_describes[i] for i in enabled_systems]
    presents = [raw_presents[i] for i in enabled_systems]

    # Indices for SkyPilot services that require a name from --service-names
    # In all_systems: indices 2 through 15 correspond to original SkyPilot services
    true_skypilot_indices = set(range(2, 16)) 

    sky_systems_requiring_names_count = sum(1 for s_idx in enabled_systems if s_idx in true_skypilot_indices)
    user_provided_sns = args.service_names

    if len(user_provided_sns) != sky_systems_requiring_names_count:
        error_msg = (
            f"Mismatch in service names. Expected {sky_systems_requiring_names_count} names "
            f"for enabled SkyPilot services that require names (indices {true_skypilot_indices} present in {enabled_systems}), "
            f"but got {len(user_provided_sns)} names: {user_provided_sns}.\n"
            f"Enabled systems (indices from all_systems): {enabled_systems}\n"
            f"Corresponding descriptions: {describes}"
        )
        # Only raise error if names were expected OR if names were provided but not expected
        if sky_systems_requiring_names_count > 0 or (sky_systems_requiring_names_count == 0 and len(user_provided_sns) > 0) :
            raise ValueError(error_msg)


    # Prepare `processed_sns_list` to be parallel to `enabled_systems`.
    # It will contain actual names for SkyPilot services needing them, and None otherwise.
    current_user_sns_idx = 0
    processed_sns_list = []
    for s_idx in enabled_systems:
        if s_idx in true_skypilot_indices:
            if current_user_sns_idx < len(user_provided_sns):
                processed_sns_list.append(user_provided_sns[current_user_sns_idx])
                current_user_sns_idx += 1
            else:
                # This state should ideally be caught by the previous check
                raise ValueError("Not enough service names provided for enabled SkyPilot services.")
        else:
            processed_sns_list.append(None) # Placeholder for GKE, SGL, SGL-enhanced etc.

    endpoints = [
        _get_endpoint_for_traffic(idx, processed_sns_list[i], args.gke_endpoint)
        for i, idx in enumerate(enabled_systems)
    ]
    print(endpoints)
    if any('None' in e for e in endpoints): # Should not happen if logic is correct
        raise ValueError('Some endpoints are None, indicating an issue in endpoint generation.')

    name_mapping = []
// ... existing code ...
