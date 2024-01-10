"""Utilities for Scaleway instances."""
from typing import Dict, List, Optional

from sky import sky_logging

logger = sky_logging.init_logger(__name__)


class ScalewayInstance:
    """Base class for GCP instance handlers."""
    NEED_TO_STOP_STATES: List[str] = []
    NON_STOPPED_STATES: List[str] = []
    NEED_TO_TERMINATE_STATES: List[str] = []

    @classmethod
    def load_resource(cls):
        """Load the Scaleway API for the instance type.

        Do not cache the resource object, as it will not work
        when multiple threads are running.
        """
        raise NotImplementedError

    @classmethod
    def stop(
        cls,
        project_id: str,
        zone: str,
        instance: str,
    ) -> dict:
        raise NotImplementedError

    @classmethod
    def terminate(
        cls,
        project_id: str,
        zone: str,
        instance: str,
    ) -> dict:
        raise NotImplementedError

    @classmethod
    def wait_for_operation(cls, operation: dict, project_id: str,
                           zone: Optional[str]) -> bool:
        raise NotImplementedError

    @classmethod
    def filter(
        cls,
        project_id: str,
        zone: str,
        label_filters: Optional[Dict[str, str]],
        status_filters: Optional[List[str]],
        included_instances: Optional[List[str]] = None,
        excluded_instances: Optional[List[str]] = None,
    ) -> List[str]:
        raise NotImplementedError

    @classmethod
    def get_vpc_name(
        cls,
        project_id: str,
        zone: str,
        instance: str,
    ) -> str:
        raise NotImplementedError

    @classmethod
    def delete_firewall_rule(
        cls,
        project_id: str,
        firewall_rule_name: str,
    ) -> None:
        raise NotImplementedError

    @classmethod
    def create_or_update_firewall_rule(
        cls,
        firewall_rule_name: str,
        project_id: str,
        vpc_name: str,
        cluster_name_on_cloud: str,
        ports: List[str],
    ) -> dict:
        raise NotImplementedError

    @classmethod
    def add_network_tag_if_not_exist(
        cls,
        project_id: str,
        zone: str,
        instance: str,
        tag: str,
    ) -> None:
        raise NotImplementedError
