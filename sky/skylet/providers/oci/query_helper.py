"""
Helper class for some OCI operations methods which needs to be shared/called 
by multiple places. 

History:
 - Hysun He (hysun.he@oracle.com) @ Apr, 2023: Initial implementation
 
"""

import logging
from datetime import datetime
import pandas as pd
import re
import oci
from sky.skylet.providers.oci.config import oci_conf
from sky.skylet.providers.oci import utils
from sky.adaptors import oci as oci_adaptor

logger = logging.getLogger(__name__)


class oci_query_helper:

    # Call Cloud API to try getting the satisfied nodes.
    @classmethod
    @utils.debug_enabled(logger=logger)
    def query_instances_by_tags(cls, tag_filters, region):
        where_clause_tags = ""
        for tag_key in tag_filters:
            if where_clause_tags != "":
                where_clause_tags += " && "
            tag_value = tag_filters[tag_key]
            where_clause_tags += f"""(freeformTags.key = '{tag_key}' && freeformTags.value = '{tag_value}')"""

        qv_str = f"""query instance resources where {where_clause_tags} && (lifecycleState != 'TERMINATED' 
                && lifecycleState != 'TERMINATING')"""
        logger.debug(f"* qv_str is {qv_str}")

        qv = oci.resource_search.models.StructuredSearchDetails(
            query=qv_str,
            type="Structured",
            matching_context_type=oci.resource_search.models.SearchDetails.
            MATCHING_CONTEXT_TYPE_NONE,
        )

        list_instances_response = oci_adaptor.get_search_client(
            region, oci_conf.get_profile()).search_resources(qv)
        result_set = list_instances_response.data.items
        logger.debug(f"* Query result: {result_set}")

        return result_set

    @classmethod
    def terminate_instances_by_tags(cls, tag_filters, region) -> int:
        logger.info(f"* terminate_instances_by_tags: {tag_filters}")
        insts = cls.query_instances_by_tags(tag_filters, region)
        fail_count = 0
        for inst in insts:
            inst_id = inst.identifier
            logger.debug(f"* Got instance(to be terminated): {inst_id}")

            try:
                oci_adaptor.get_core_client(
                    region, oci_conf.get_profile()).terminate_instance(inst_id)
            except Exception as e:
                logger.error(f"!!! Terminate instance failed: {inst_id}")
                logger.error(f"!!! {str(e)}")
                logger.error(inst)
                fail_count += 1

        if fail_count == 0:
            logger.info(f"* terminate_instances_by_tags success: {tag_filters}")
        else:
            logger.warn(
                f"! Attention: {fail_count} instances in the cluster failed to be terminate!"
            )

        return fail_count

    @classmethod
    @utils.debug_enabled(logger=logger)
    def subscribe_image(cls, compartment_id, listing_id, resource_version,
                        region):
        if (pd.isna(listing_id) or listing_id.strip() == "None" or
                listing_id.strip() == "nan"):
            logger.debug("* listing_id not specified.")
            return

        core_client = oci_adaptor.get_core_client(region,
                                                  oci_conf.get_profile())
        try:
            agreements_response = core_client.get_app_catalog_listing_agreements(
                listing_id=listing_id, resource_version=resource_version)
            agreements = agreements_response.data

            core_client.create_app_catalog_subscription(
                create_app_catalog_subscription_details=oci.core.models.
                CreateAppCatalogSubscriptionDetails(
                    compartment_id=compartment_id,
                    listing_id=listing_id,
                    listing_resource_version=agreements.
                    listing_resource_version,
                    oracle_terms_of_use_link=agreements.
                    oracle_terms_of_use_link,
                    time_retrieved=datetime.strptime(
                        re.sub(
                            "\d{3}\+\d{2}\:\d{2}",
                            "Z",
                            str(agreements.time_retrieved),
                            0,
                        ),
                        "%Y-%m-%d %H:%M:%S.%fZ",
                    ),
                    signature=agreements.signature,
                    eula_link=agreements.eula_link,
                ))
        except Exception as e:
            logger.critical(
                f"! subscribe_image: {listing_id} - {resource_version} ... [Failed]"
                f"! Error message: {str(e)}")
            raise RuntimeError("ERR: Image subscription error!")

        logger.debug(
            f"* subscribe_image: {listing_id} - {resource_version} ... [Done]")

    @classmethod
    @utils.debug_enabled(logger=logger)
    def find_vcn_subnet(cls, region):
        # TODO: Query subnet id according to the skypilot-vcn
        return oci_conf.get_vcn_subnet(region)
