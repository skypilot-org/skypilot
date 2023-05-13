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
    def find_compartment(cls, region) -> str:
        """ If compartment is not configured, we use root compartment """
        # Try to use the configured one first
        skypilot_compartment = oci_conf.get_compartment(region)
        if skypilot_compartment is not None:
            return skypilot_compartment
        
        # If not specified, we try to find the one skypilot-compartment
        root = oci_adaptor.get_oci_config(region)['tenancy']
        list_compartments_response = oci_conf.identity_client.list_compartments(
            compartment_id=root,
            name=oci_conf.COMPARTMENT,
            lifecycle_state='ACTIVE',
            limit=1
        )
        compartments = list_compartments_response.data
        if len(compartments) > 0:
            skypilot_compartment = compartments[0].compartmentId
            return skypilot_compartment
        
        # Finally, we use root compartment none matches above 
        skypilot_compartment = root
        return skypilot_compartment
    

    @classmethod
    @utils.debug_enabled(logger=logger)
    def find_create_vcn_subnet(cls, region) -> str:
        """ If sub is not configured, we find/create VCN skypilot_vcn """
        subnet = oci_conf.get_vcn_subnet(region)
        if subnet is not None:
            return subnet

        net_client = oci_adaptor.get_net_client(region, oci_conf.get_profile())
        skypilot_compartment = cls.find_compartment(region)
        list_vcns_response = net_client.list_vcns(
            compartment_id=skypilot_compartment,
            display_name=oci_conf.VCN_NAME,
            lifecycle_state="AVAILABLE")
        vcns = list_vcns_response.data
        if len(vcns) > 0:
            skypilot_vcn = vcns[0].id
            list_subnets_response = net_client.list_subnets(
                compartment_id=skypilot_compartment,
                limit=1,
                vcn_id=skypilot_vcn,
                display_name=oci_conf.VCN_SUBNET_NAME,
                lifecycle_state="AVAILABLE")
            logger.debug(f'Got VCN subnet \n{list_subnets_response.data}')
            subnet = list_subnets_response.data[0].id
        else:
            create_vcn_response = net_client.create_vcn(
                create_vcn_details=oci.core.models.CreateVcnDetails(
                    compartment_id=skypilot_compartment,
                    cidr_blocks=[oci_conf.VCN_CIDR],
                    display_name=oci_conf.VCN_NAME,
                    is_ipv6_enabled=False,
                    dns_label=oci_conf.VCN_DNS_LABEL))
            vcn_data = create_vcn_response.data
            skypilot_vcn = vcn_data.id
            route_table = vcn_data.default_route_table_id
            security_list = vcn_data.default_security_list_id
            dhcp_options_id = vcn_data.default_dhcp_options_id
            logger.debug(f'Created VCN \n{vcn_data}')

            create_ig_response = net_client.create_internet_gateway(
                create_internet_gateway_details=oci.core.models.
                CreateInternetGatewayDetails(
                    compartment_id=skypilot_compartment,
                    is_enabled=True,
                    vcn_id=skypilot_vcn,
                    display_name=oci_conf.VCN_INTERNET_GATEWAY_NAME,
                    route_table_id=route_table))
            logger.debug(
                f'Created internet gateway \n{create_ig_response.data}')

            create_subnet_response = net_client.create_subnet(
                create_subnet_details=oci.core.models.CreateSubnetDetails(
                    cidr_block=oci_conf.VCN_SUBNET_CIDR,
                    compartment_id=skypilot_compartment,
                    vcn_id=skypilot_vcn,
                    dhcp_options_id=dhcp_options_id,
                    display_name=oci_conf.VCN_SUBNET_NAME,
                    prohibit_internet_ingress=False,
                    prohibit_public_ip_on_vnic=False,
                    route_table_id=route_table,
                    security_list_ids=[security_list]))
            logger.debug(f'Created subnet \n{create_subnet_response.data}')
            subnet = create_subnet_response.data.id

        return subnet
