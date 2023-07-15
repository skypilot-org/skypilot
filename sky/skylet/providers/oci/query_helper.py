"""
Helper class for some OCI operations methods which needs to be shared/called 
by multiple places. 

History:
 - Hysun He (hysun.he@oracle.com) @ Apr, 2023: Initial implementation
 
"""

import logging
import traceback
import time
from datetime import datetime
import pandas as pd
import re
from typing import Optional
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
            where_clause_tags += (f"(freeformTags.key = '{tag_key}'"
                                  f" && freeformTags.value = '{tag_value}')")

        qv_str = (f"query instance resources where {where_clause_tags}"
                  f" && (lifecycleState != 'TERMINATED'"
                  f" && lifecycleState != 'TERMINATING')")

        qv = oci_adaptor.get_oci(
        ).resource_search.models.StructuredSearchDetails(
            query=qv_str,
            type="Structured",
            matching_context_type=oci_adaptor.get_oci().resource_search.models.
            SearchDetails.MATCHING_CONTEXT_TYPE_NONE,
        )

        list_instances_response = oci_adaptor.get_search_client(
            region, oci_conf.get_profile()).search_resources(qv)
        result_set = list_instances_response.data.items

        return result_set

    @classmethod
    def terminate_instances_by_tags(cls, tag_filters, region) -> int:
        logger.debug(f"Terminate instance by tags: {tag_filters}")
        insts = cls.query_instances_by_tags(tag_filters, region)
        fail_count = 0
        for inst in insts:
            inst_id = inst.identifier
            logger.debug(f"Got instance(to be terminated): {inst_id}")

            try:
                oci_adaptor.get_core_client(
                    region, oci_conf.get_profile()).terminate_instance(inst_id)
            except Exception as e:
                fail_count += 1
                logger.error(f"Terminate instance failed: {str(e)}\n: {inst}")
                traceback.print_exc()

        if fail_count == 0:
            logger.debug(f"Instance teardown result: OK")
        else:
            logger.warn(f"Instance teardown result: {fail_count} failed!")

        return fail_count

    @classmethod
    @utils.debug_enabled(logger=logger)
    def subscribe_image(cls, compartment_id, listing_id, resource_version,
                        region):
        if (pd.isna(listing_id) or listing_id.strip() == "None" or
                listing_id.strip() == "nan"):
            return

        core_client = oci_adaptor.get_core_client(region,
                                                  oci_conf.get_profile())
        try:
            agreements_response = core_client.get_app_catalog_listing_agreements(
                listing_id=listing_id, resource_version=resource_version)
            agreements = agreements_response.data

            core_client.create_app_catalog_subscription(
                create_app_catalog_subscription_details=oci_adaptor.get_oci(
                ).core.models.CreateAppCatalogSubscriptionDetails(
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
                f"subscribe_image: {listing_id} - {resource_version} ... [Failed]"
                f"Error message: {str(e)}")
            raise RuntimeError("ERR: Image subscription error!")

    @classmethod
    @utils.debug_enabled(logger=logger)
    def find_compartment(cls, region) -> str:
        """ If compartment is not configured, we use root compartment """
        # Try to use the configured one first
        skypilot_compartment = oci_conf.get_compartment(region)
        if skypilot_compartment is not None:
            return skypilot_compartment

        # If not specified, we try to find the one skypilot-compartment
        # Pass-in a profile parameter so that multiple profile in oci
        # config file is supported (2023/06/09).
        root = oci_adaptor.get_oci_config(region,
                                          oci_conf.get_profile())['tenancy']
        list_compartments_response = oci_adaptor.get_identity_client(
            region,
            oci_conf.get_profile()).list_compartments(compartment_id=root,
                                                      name=oci_conf.COMPARTMENT,
                                                      lifecycle_state='ACTIVE',
                                                      limit=1)
        compartments = list_compartments_response.data
        if len(compartments) > 0:
            skypilot_compartment = compartments[0].id
            return skypilot_compartment

        # Finally, we use root compartment none matches above
        skypilot_compartment = root
        return skypilot_compartment

    @classmethod
    @utils.debug_enabled(logger=logger)
    def find_create_vcn_subnet(cls, region) -> Optional[str]:
        """ If sub is not configured, we find/create VCN skypilot_vcn """
        subnet = oci_conf.get_vcn_subnet(region)
        if subnet is not None:
            # User explicitly specified the subnet in sky config.
            return subnet

        # Try to reuse the skypilot_vcn.
        net_client = oci_adaptor.get_net_client(region, oci_conf.get_profile())
        skypilot_compartment = cls.find_compartment(region)
        list_vcns_response = net_client.list_vcns(
            compartment_id=skypilot_compartment,
            display_name=oci_conf.VCN_NAME,
            lifecycle_state="AVAILABLE")
        vcns = list_vcns_response.data
        if len(vcns) > 0:
            # Found the VCN.
            skypilot_vcn = vcns[0].id
            list_subnets_response = net_client.list_subnets(
                compartment_id=skypilot_compartment,
                limit=1,
                vcn_id=skypilot_vcn,
                display_name=oci_conf.VCN_SUBNET_NAME,
                lifecycle_state="AVAILABLE")
            logger.debug(f'Got VCN subnet \n{list_subnets_response.data}')
            if len(list_subnets_response.data) < 1:
                logger.error(f'No subnet {oci_conf.VCN_SUBNET_NAME} '
                             f'found in the VCN {oci_conf.VCN_NAME}')
                raise RuntimeError(f'VcnSubnetNotFound Error: No subnet '
                                   f'{oci_conf.VCN_SUBNET_NAME} found in '
                                   f'the VCN {oci_conf.VCN_NAME}')
            subnet = list_subnets_response.data[0].id
            return subnet
        else:
            # Create the skypilot_vcn and related resources
            return cls.create_vcn_subnet(net_client, skypilot_compartment)

    @classmethod
    @utils.debug_enabled(logger=logger)
    def create_vcn_subnet(cls, net_client,
                          skypilot_compartment) -> Optional[str]:
        try:
            create_vcn_response = net_client.create_vcn(
                create_vcn_details=oci_adaptor.get_oci().core.models.
                CreateVcnDetails(compartment_id=skypilot_compartment,
                                 cidr_blocks=[oci_conf.VCN_CIDR],
                                 display_name=oci_conf.VCN_NAME,
                                 is_ipv6_enabled=False,
                                 dns_label=oci_conf.VCN_DNS_LABEL))
            vcn_data = create_vcn_response.data
            logger.debug(f'Created VCN \n{vcn_data}')
            skypilot_vcn = vcn_data.id
            route_table = vcn_data.default_route_table_id
            security_list = vcn_data.default_security_list_id
            dhcp_options_id = vcn_data.default_dhcp_options_id

            # Create internet gateway for internet access
            create_ig_response = net_client.create_internet_gateway(
                create_internet_gateway_details=oci_adaptor.get_oci(
                ).core.models.CreateInternetGatewayDetails(
                    compartment_id=skypilot_compartment,
                    is_enabled=True,
                    vcn_id=skypilot_vcn,
                    display_name=oci_conf.VCN_INTERNET_GATEWAY_NAME))
            logger.debug(
                f'Created internet gateway \n{create_ig_response.data}')
            ig = create_ig_response.data.id

            # Create a public subnet.
            create_subnet_response = net_client.create_subnet(
                create_subnet_details=oci_adaptor.get_oci().core.models.
                CreateSubnetDetails(cidr_block=oci_conf.VCN_SUBNET_CIDR,
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

            list_services_response = net_client.list_services(limit=100)
            services = [
                s for s in list_services_response.data
                if str(s.cidr_block).startswith('all-') and str(s.cidr_block).
                endswith('-services-in-oracle-services-network')
            ]
            if len(services) > 0:
                # Create service gateway for regional services.
                create_sg_response = net_client.create_service_gateway(
                    create_service_gateway_details=oci_adaptor.get_oci(
                    ).core.models.CreateServiceGatewayDetails(
                        compartment_id=skypilot_compartment,
                        services=[
                            oci_adaptor.get_oci().core.models.
                            ServiceIdRequestDetails(service_id=services[0].id)
                        ],
                        vcn_id=skypilot_vcn))
                logger.debug(f'Service Gateway: \n{create_sg_response.data}')
                sg = create_sg_response.data.id

            # Update security list: Allow all traffic in the same subnet
            update_security_list_response = net_client.update_security_list(
                security_list_id=security_list,
                update_security_list_details=oci_adaptor.get_oci(
                ).core.models.UpdateSecurityListDetails(ingress_security_rules=[
                    oci_adaptor.get_oci().core.models.IngressSecurityRule(
                        protocol="6",
                        source=oci_conf.VCN_CIDR_INTERNET,
                        is_stateless=False,
                        source_type="CIDR_BLOCK",
                        tcp_options=oci_adaptor.get_oci().core.models.
                        TcpOptions(destination_port_range=oci_adaptor.get_oci().
                                   core.models.PortRange(max=22, min=22),
                                   source_port_range=oci_adaptor.get_oci(
                                   ).core.models.PortRange(max=65535, min=1)),
                        description="Allow SSH port."),
                    oci_adaptor.get_oci().core.models.IngressSecurityRule(
                        protocol="all",
                        source=oci_conf.VCN_SUBNET_CIDR,
                        is_stateless=False,
                        source_type="CIDR_BLOCK",
                        description="Allow all traffic from/to same subnet."),
                    oci_adaptor.get_oci().core.models.IngressSecurityRule(
                        protocol="1",
                        source=oci_conf.VCN_CIDR_INTERNET,
                        is_stateless=False,
                        source_type="CIDR_BLOCK",
                        icmp_options=oci_adaptor.get_oci(
                        ).core.models.IcmpOptions(type=3, code=4),
                        description="ICMP traffic."),
                    oci_adaptor.get_oci().core.models.IngressSecurityRule(
                        protocol="1",
                        source=oci_conf.VCN_CIDR,
                        is_stateless=False,
                        source_type="CIDR_BLOCK",
                        icmp_options=oci_adaptor.get_oci(
                        ).core.models.IcmpOptions(type=3),
                        description="ICMP traffic (VCN)."),
                ]))
            logger.debug(
                f'Updated security_list: \n{update_security_list_response.data}'
            )

            # Update route table: bind to the internet gateway
            update_route_table_response = net_client.update_route_table(
                rt_id=route_table,
                update_route_table_details=oci_adaptor.get_oci(
                ).core.models.UpdateRouteTableDetails(route_rules=[
                    oci_adaptor.get_oci().core.models.RouteRule(
                        network_entity_id=create_ig_response.data.id,
                        destination='0.0.0.0/0',
                        destination_type='CIDR_BLOCK',
                        description='Route table for SkyPilot VCN',
                        route_type='STATIC')
                ]))
            logger.debug(f'Route table: \n{update_route_table_response.data}')

        except oci_adaptor.service_exception() as e:
            logger.error(f'Create VCN Error: Create new VCN '
                         f'{oci_conf.VCN_NAME} failed: {str(e)}')
            # In case of partial success while creating vcn
            cls.delete_vcn(net_client, skypilot_vcn, subnet, ig, sg)
            subnet = None

        return subnet

    @classmethod
    @utils.debug_enabled(logger=logger)
    def delete_vcn(cls, net_client, skypilot_vcn, skypilot_subnet,
                   internet_gateway, service_gateway):
        if skypilot_vcn is None:
            return  # Nothing to delete
        try:
            if internet_gateway is not None:
                # Delete internet gateway
                delete_ig_response = net_client.delete_internet_gateway(
                    ig_id=internet_gateway)
                logger.debug(f'Deleted internet gateway {internet_gateway}'
                             f'-{delete_ig_response.data}')
            if service_gateway is not None:
                # Delete service gateway
                delete_sg_response = net_client.delete_service_gateway(
                    service_gateway_id=service_gateway)
                logger.debug(f'Deleted service gateway {service_gateway}'
                             f'-{delete_sg_response.data}')
            if skypilot_subnet is not None:
                # Delete subnet
                delete_subnet_response = net_client.delete_subnet(
                    subnet_id=skypilot_subnet)
                logger.debug(f'Deleted subnet {skypilot_subnet}'
                             f'-{delete_subnet_response.data}')
            # Delete vcn
            retry_count = 0
            while retry_count < oci_conf.MAX_RETRY_COUNT:
                try:
                    delete_vcn_response = net_client.delete_vcn(
                        vcn_id=skypilot_vcn)
                    logger.debug(
                        f'Deleted vcn {skypilot_vcn}-{delete_vcn_response.data}'
                    )
                    break
                except oci_adaptor.service_exception() as e:
                    logger.info(f'Waiting del SG/IG/Subnet finish: {str(e)}')
                    retry_count = retry_count + 1
                    if retry_count == oci_conf.MAX_RETRY_COUNT:
                        raise e
                    else:
                        time.sleep(oci_conf.RETRY_INTERVAL_BASE_SECONDS)

        except oci_adaptor.service_exception() as e:
            logger.error(f'Delete VCN {oci_conf.VCN_NAME} Error: {str(e)}')
