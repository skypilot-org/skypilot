from typing import Any, Dict, List, Optional

def cleanup_ports(
    provider_name: str,
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """Delete any opened ports for the Simplepod provider."""
    print(f"Cleaning up ports {ports} for cluster '{cluster_name_on_cloud}' on Simplepod.")
    # Add actual logic to interact with Simplepod's API to clean up ports.
