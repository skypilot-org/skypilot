"""OCI query helper class

History:
 - Hysun He (hysun.he@oracle.com) @ Oct.16, 2024: Code here mainly
   migrated from the old provisioning API.
 - Hysun He (hysun.he@oracle.com) @ Oct.18, 2024: Enhancement.
   find_compartment: allow search subtree when find a compartment.
 - Hysun He (hysun.he@oracle.com) @ Nov.12, 2024: Add methods to
   Add/remove security rules: create_nsg_rules & remove_nsg
"""
from datetime import datetime
import functools
from logging import Logger
import re
import time
import traceback
import typing
from typing import List, Optional, Tuple

from sky import exceptions
from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.adaptors import oci as oci_adaptor
from sky.clouds.utils import oci_utils
from sky.provision import constants
from sky.utils import resources_utils

if typing.TYPE_CHECKING:
    import pandas as pd
else:
    pd = adaptors_common.LazyImport('pandas')

logger = sky_logging.init_logger(__name__)


def debug_enabled(log: Logger):

    def decorate(f):

        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            dt_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            log.debug(f'{dt_str} Enter {f}, {args}, {kwargs}')
            try:
                return f(*args, **kwargs)
            finally:
                log.debug(f'{dt_str} Exit {f}')

        return wrapper

    return decorate


class QueryHelper:
    """Helper class for some OCI operations
    """
    # Call Cloud API to try getting the satisfied nodes.
    @classmethod
    @debug_enabled(logger)
    def query_instances_by_tags(cls, tag_filters, region):

        where_clause_tags = ''
        for tag_key in tag_filters:
            if where_clause_tags != '':
                where_clause_tags += ' && '

            tag_value = tag_filters[tag_key]
            where_clause_tags += (f'(freeformTags.key = \'{tag_key}\''
                                  f' && freeformTags.value = \'{tag_value}\')')

        qv_str = (f'query instance resources where {where_clause_tags}'
                  f' && (lifecycleState != \'TERMINATED\''
                  f' && lifecycleState != \'TERMINATING\')')

        qv = oci_adaptor.oci.resource_search.models.StructuredSearchDetails(
            query=qv_str,
            type='Structured',
            matching_context_type=oci_adaptor.oci.resource_search.models.
            SearchDetails.MATCHING_CONTEXT_TYPE_NONE,
        )

        list_instances_response = oci_adaptor.get_search_client(
            region, oci_utils.oci_config.get_profile()).search_resources(qv)
        result_set = list_instances_response.data.items

        return result_set

    @classmethod
    @debug_enabled(logger)
    def terminate_instances_by_tags(cls, tag_filters, region) -> int:
        logger.debug(f'Terminate instance by tags: {tag_filters}')

        cluster_name = tag_filters[constants.TAG_RAY_CLUSTER_NAME]
        nsg_name = oci_utils.oci_config.NSG_NAME_TEMPLATE.format(
            cluster_name=cluster_name)
        nsg_id = cls.find_nsg(region, nsg_name, create_if_not_exist=False)

        core_client = oci_adaptor.get_core_client(
            region, oci_utils.oci_config.get_profile())

        insts = cls.query_instances_by_tags(tag_filters, region)
        fail_count = 0
        for inst in insts:
            inst_id = inst.identifier
            logger.debug(f'Terminating instance {inst_id}')

            try:
                # Release the NSG reference so that the NSG can be
                # deleted without waiting the instance being terminated.
                if nsg_id is not None:
                    cls.detach_nsg(region, inst, nsg_id)

                # Terminate the instance
                core_client.terminate_instance(inst_id)

            except oci_adaptor.oci.exceptions.ServiceError as e:
                fail_count += 1
                logger.error(f'Terminate instance failed: {str(e)}\n: {inst}')
                traceback.print_exc()

        if fail_count == 0:
            logger.debug('Instance teardown result: OK')
        else:
            logger.warning(f'Instance teardown result: {fail_count} failed!')

        return fail_count

    @classmethod
    @debug_enabled(logger)
    def launch_instance(cls, region, launch_config):
        """ To create a new instance """
        return oci_adaptor.get_core_client(
            region, oci_utils.oci_config.get_profile()).launch_instance(
                launch_instance_details=launch_config)

    @classmethod
    @debug_enabled(logger)
    def start_instance(cls, region, instance_id):
        """ To start an existing instance """
        return oci_adaptor.get_core_client(
            region, oci_utils.oci_config.get_profile()).instance_action(
                instance_id=instance_id, action='START')

    @classmethod
    @debug_enabled(logger)
    def stop_instance(cls, region, instance_id):
        """ To stop an instance """
        return oci_adaptor.get_core_client(
            region, oci_utils.oci_config.get_profile()).instance_action(
                instance_id=instance_id, action='STOP')

    @classmethod
    @debug_enabled(logger)
    def wait_instance_until_status(cls, region, node_id, status):
        """ To wait a instance becoming the specified state """
        compute_client = oci_adaptor.get_core_client(
            region, oci_utils.oci_config.get_profile())

        resp = compute_client.get_instance(instance_id=node_id)

        oci_adaptor.oci.wait_until(
            compute_client,
            resp,
            'lifecycle_state',
            status,
        )

    @classmethod
    def get_instance_primary_vnic(cls, region, inst_info):
        """ Get the primary vnic infomation of the instance """
        list_vnic_attachments_response = oci_adaptor.get_core_client(
            region, oci_utils.oci_config.get_profile()).list_vnic_attachments(
                availability_domain=inst_info['ad'],
                compartment_id=inst_info['compartment'],
                instance_id=inst_info['inst_id'],
            )
        vnic = list_vnic_attachments_response.data[0]
        return oci_adaptor.get_net_client(
            region, oci_utils.oci_config.get_profile()).get_vnic(
                vnic_id=vnic.vnic_id).data

    @classmethod
    @debug_enabled(logger)
    def subscribe_image(cls, compartment_id, listing_id, resource_version,
                        region):
        if (pd.isna(listing_id) or listing_id.strip() == 'None' or
                listing_id.strip() == 'nan'):
            return

        core_client = oci_adaptor.get_core_client(
            region, oci_utils.oci_config.get_profile())
        try:
            agreements_resp = core_client.get_app_catalog_listing_agreements(
                listing_id=listing_id, resource_version=resource_version)
            agreements = agreements_resp.data

            core_client.create_app_catalog_subscription(
                create_app_catalog_subscription_details=oci_adaptor.oci.core.
                models.CreateAppCatalogSubscriptionDetails(
                    compartment_id=compartment_id,
                    listing_id=listing_id,
                    listing_resource_version=agreements.
                    listing_resource_version,
                    oracle_terms_of_use_link=agreements.
                    oracle_terms_of_use_link,
                    time_retrieved=datetime.strptime(
                        re.sub(
                            r'\d{3}\+\d{2}\:\d{2}',
                            'Z',
                            str(agreements.time_retrieved),
                            0,
                        ),
                        '%Y-%m-%d %H:%M:%S.%fZ',
                    ),
                    signature=agreements.signature,
                    eula_link=agreements.eula_link,
                ))
        except oci_adaptor.oci.exceptions.ServiceError as e:
            logger.critical(
                f'[Failed] subscribe_image: {listing_id} - {resource_version}'
                f'Error message: {str(e)}')
            raise RuntimeError('ERR: Image subscription error!') from e

    @classmethod
    @debug_enabled(logger)
    def find_compartment(cls, region) -> str:
        """ If compartment is not configured, we use root compartment """
        # Try to use the configured one first
        skypilot_compartment = oci_utils.oci_config.get_compartment(region)
        if skypilot_compartment is not None:
            return skypilot_compartment

        # If not specified, we try to find the one skypilot-compartment
        # Pass-in a profile parameter so that multiple profile in oci
        # config file is supported (2023/06/09).
        root = oci_adaptor.get_oci_config(
            region, oci_utils.oci_config.get_profile())['tenancy']

        list_compartments_response = oci_adaptor.get_identity_client(
            region, oci_utils.oci_config.get_profile()).list_compartments(
                compartment_id=root,
                name=oci_utils.oci_config.COMPARTMENT,
                compartment_id_in_subtree=True,
                access_level='ACCESSIBLE',
                lifecycle_state='ACTIVE',
                sort_by='TIMECREATED',
                sort_order='DESC',
                limit=1)

        compartments = list_compartments_response.data
        if len(compartments) > 0:
            skypilot_compartment = compartments[0].id
            return skypilot_compartment

        # Finally, we use root compartment none matches above
        skypilot_compartment = root
        return skypilot_compartment

    @classmethod
    @debug_enabled(logger)
    def find_create_vcn_subnet(cls, region) -> Optional[str]:
        """ If sub is not configured, we find/create VCN skypilot_vcn """
        subnet = oci_utils.oci_config.get_vcn_subnet(region)
        if subnet is not None:
            # User explicitly specified the subnet in sky config.
            return subnet

        # Try to reuse the skypilot_vcn.
        net_client = oci_adaptor.get_net_client(
            region, oci_utils.oci_config.get_profile())
        skypilot_compartment = cls.find_compartment(region)
        list_vcns_response = net_client.list_vcns(
            compartment_id=skypilot_compartment,
            display_name=oci_utils.oci_config.VCN_NAME,
            lifecycle_state='AVAILABLE')
        vcns = list_vcns_response.data
        if len(vcns) > 0:
            # Found the VCN.
            skypilot_vcn = vcns[0].id
            list_subnets_response = net_client.list_subnets(
                compartment_id=skypilot_compartment,
                limit=1,
                vcn_id=skypilot_vcn,
                display_name=oci_utils.oci_config.VCN_SUBNET_NAME,
                lifecycle_state='AVAILABLE')
            logger.debug(f'Got VCN subnet \n{list_subnets_response.data}')
            if len(list_subnets_response.data) < 1:
                logger.error(
                    f'No subnet {oci_utils.oci_config.VCN_SUBNET_NAME} '
                    f'found in the VCN {oci_utils.oci_config.VCN_NAME}')
                raise RuntimeError(
                    f'VcnSubnetNotFound Error: No subnet '
                    f'{oci_utils.oci_config.VCN_SUBNET_NAME} found in '
                    f'the VCN {oci_utils.oci_config.VCN_NAME}')
            subnet = list_subnets_response.data[0].id
            return subnet
        else:
            # Create the skypilot_vcn and related resources
            return cls.create_vcn_subnet(net_client, skypilot_compartment)

    @classmethod
    @debug_enabled(logger)
    def create_vcn_subnet(cls, net_client,
                          skypilot_compartment) -> Optional[str]:

        skypilot_vcn = None  # VCN for the resources
        subnet = None  # Subnet for the VMs
        ig = None  # Internet gateway
        sg = None  # Service gateway

        try:
            # pylint: disable=line-too-long
            create_vcn_response = net_client.create_vcn(
                create_vcn_details=oci_adaptor.oci.core.models.CreateVcnDetails(
                    compartment_id=skypilot_compartment,
                    cidr_blocks=[oci_utils.oci_config.VCN_CIDR],
                    display_name=oci_utils.oci_config.VCN_NAME,
                    is_ipv6_enabled=False,
                    dns_label=oci_utils.oci_config.VCN_DNS_LABEL))
            vcn_data = create_vcn_response.data
            logger.debug(f'Created VCN \n{vcn_data}')
            skypilot_vcn = vcn_data.id
            route_table = vcn_data.default_route_table_id
            security_list = vcn_data.default_security_list_id
            dhcp_options_id = vcn_data.default_dhcp_options_id

            # Create internet gateway for internet access
            create_ig_response = net_client.create_internet_gateway(
                create_internet_gateway_details=oci_adaptor.oci.core.models.
                CreateInternetGatewayDetails(
                    compartment_id=skypilot_compartment,
                    is_enabled=True,
                    vcn_id=skypilot_vcn,
                    display_name=oci_utils.oci_config.VCN_INTERNET_GATEWAY_NAME
                ))
            logger.debug(
                f'Created internet gateway \n{create_ig_response.data}')
            ig = create_ig_response.data.id

            # Create a public subnet.
            create_subnet_response = net_client.create_subnet(
                create_subnet_details=oci_adaptor.oci.core.models.
                CreateSubnetDetails(
                    cidr_block=oci_utils.oci_config.VCN_SUBNET_CIDR,
                    compartment_id=skypilot_compartment,
                    vcn_id=skypilot_vcn,
                    dhcp_options_id=dhcp_options_id,
                    display_name=oci_utils.oci_config.VCN_SUBNET_NAME,
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
                    create_service_gateway_details=oci_adaptor.oci.core.models.
                    CreateServiceGatewayDetails(
                        compartment_id=skypilot_compartment,
                        services=[
                            oci_adaptor.oci.core.models.ServiceIdRequestDetails(
                                service_id=services[0].id)
                        ],
                        vcn_id=skypilot_vcn))
                logger.debug(f'Service Gateway: \n{create_sg_response.data}')
                sg = create_sg_response.data.id

            # Update security list: Allow all traffic in the same subnet
            update_security_list_response = net_client.update_security_list(
                security_list_id=security_list,
                update_security_list_details=oci_adaptor.oci.core.models.
                UpdateSecurityListDetails(ingress_security_rules=[
                    oci_adaptor.oci.core.models.IngressSecurityRule(
                        protocol='6',
                        source=oci_utils.oci_config.VCN_CIDR_INTERNET,
                        is_stateless=False,
                        source_type='CIDR_BLOCK',
                        tcp_options=oci_adaptor.oci.core.models.TcpOptions(
                            destination_port_range=oci_adaptor.oci.core.models.
                            PortRange(max=22, min=22),
                            source_port_range=oci_adaptor.oci.core.models.
                            PortRange(max=65535, min=1)),
                        description='Allow SSH port.'),
                    oci_adaptor.oci.core.models.IngressSecurityRule(
                        protocol='all',
                        source=oci_utils.oci_config.VCN_SUBNET_CIDR,
                        is_stateless=False,
                        source_type='CIDR_BLOCK',
                        description='Allow all traffic from/to same subnet.'),
                    oci_adaptor.oci.core.models.IngressSecurityRule(
                        protocol='1',
                        source=oci_utils.oci_config.VCN_CIDR_INTERNET,
                        is_stateless=False,
                        source_type='CIDR_BLOCK',
                        icmp_options=oci_adaptor.oci.core.models.IcmpOptions(
                            type=3, code=4),
                        description='ICMP traffic.'),
                    oci_adaptor.oci.core.models.IngressSecurityRule(
                        protocol='1',
                        source=oci_utils.oci_config.VCN_CIDR,
                        is_stateless=False,
                        source_type='CIDR_BLOCK',
                        icmp_options=oci_adaptor.oci.core.models.IcmpOptions(
                            type=3),
                        description='ICMP traffic (VCN).'),
                ]))
            logger.debug(
                f'Updated security_list: \n{update_security_list_response.data}'
            )

            # Update route table: bind to the internet gateway
            update_route_table_response = net_client.update_route_table(
                rt_id=route_table,
                update_route_table_details=oci_adaptor.oci.core.models.
                UpdateRouteTableDetails(route_rules=[
                    oci_adaptor.oci.core.models.RouteRule(
                        network_entity_id=create_ig_response.data.id,
                        destination='0.0.0.0/0',
                        destination_type='CIDR_BLOCK',
                        description='Route table for SkyPilot VCN',
                        route_type='STATIC')
                ]))
            logger.debug(f'Route table: \n{update_route_table_response.data}')

        except oci_adaptor.oci.exceptions.ServiceError as e:
            logger.error(f'Create VCN Error: Create new VCN '
                         f'{oci_utils.oci_config.VCN_NAME} failed: {str(e)}')
            # In case of partial success while creating vcn
            cls.delete_vcn(net_client, skypilot_vcn, subnet, ig, sg)
            subnet = None

        return subnet

    @classmethod
    @debug_enabled(logger)
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
            while retry_count < oci_utils.oci_config.MAX_RETRY_COUNT:
                try:
                    delete_vcn_response = net_client.delete_vcn(
                        vcn_id=skypilot_vcn)
                    logger.debug(
                        f'Deleted vcn {skypilot_vcn}-{delete_vcn_response.data}'
                    )
                    break
                except oci_adaptor.oci.exceptions.ServiceError as e:
                    logger.info(f'Waiting del SG/IG/Subnet finish: {str(e)}')
                    retry_count = retry_count + 1
                    if retry_count == oci_utils.oci_config.MAX_RETRY_COUNT:
                        raise e
                    else:
                        time.sleep(
                            oci_utils.oci_config.RETRY_INTERVAL_BASE_SECONDS)

        except oci_adaptor.oci.exceptions.ServiceError as e:
            logger.error(
                f'Delete VCN {oci_utils.oci_config.VCN_NAME} Error: {str(e)}')

    @classmethod
    @debug_enabled(logger)
    def find_nsg(cls, region: str, nsg_name: str,
                 create_if_not_exist: bool) -> Optional[str]:
        net_client = oci_adaptor.get_net_client(
            region, oci_utils.oci_config.get_profile())

        compartment = cls.find_compartment(region)

        list_vcns_resp = net_client.list_vcns(
            compartment_id=compartment,
            display_name=oci_utils.oci_config.VCN_NAME,
            lifecycle_state='AVAILABLE',
        )

        if not list_vcns_resp:
            raise exceptions.ResourcesUnavailableError(
                'The VCN is not available')

        # Get the primary vnic.
        assert len(list_vcns_resp.data) > 0
        vcn = list_vcns_resp.data[0]

        list_nsg_resp = net_client.list_network_security_groups(
            compartment_id=compartment,
            vcn_id=vcn.id,
            limit=1,
            display_name=nsg_name,
        )

        nsgs = list_nsg_resp.data
        if nsgs:
            assert len(nsgs) == 1
            return nsgs[0].id
        elif not create_if_not_exist:
            return None

        # Continue to create new NSG if not exists
        create_nsg_resp = net_client.create_network_security_group(
            create_network_security_group_details=oci_adaptor.oci.core.models.
            CreateNetworkSecurityGroupDetails(
                compartment_id=compartment,
                vcn_id=vcn.id,
                display_name=nsg_name,
            ))
        get_nsg_resp = net_client.get_network_security_group(
            network_security_group_id=create_nsg_resp.data.id)
        oci_adaptor.oci.wait_until(
            net_client,
            get_nsg_resp,
            'lifecycle_state',
            'AVAILABLE',
        )

        return get_nsg_resp.data.id

    @classmethod
    def get_range_min_max(cls, port_range: str) -> Tuple[int, int]:
        range_list = port_range.split('-')
        if len(range_list) == 1:
            return (int(range_list[0]), int(range_list[0]))
        from_port, to_port = range_list
        return (int(from_port), int(to_port))

    @classmethod
    @debug_enabled(logger)
    def create_nsg_rules(cls, region: str, cluster_name: str,
                         ports: List[str]) -> None:
        """ Create per-cluster NSG with ingress rules """
        if not ports:
            return

        net_client = oci_adaptor.get_net_client(
            region, oci_utils.oci_config.get_profile())

        nsg_name = oci_utils.oci_config.NSG_NAME_TEMPLATE.format(
            cluster_name=cluster_name)
        nsg_id = cls.find_nsg(region, nsg_name, create_if_not_exist=True)

        filters = {constants.TAG_RAY_CLUSTER_NAME: cluster_name}
        insts = query_helper.query_instances_by_tags(filters, region)
        for inst in insts:
            vnic = cls.get_instance_primary_vnic(
                region=region,
                inst_info={
                    'inst_id': inst.identifier,
                    'ad': inst.availability_domain,
                    'compartment': inst.compartment_id,
                })
            nsg_ids = vnic.nsg_ids
            if not nsg_ids:
                net_client.update_vnic(
                    vnic_id=vnic.id,
                    update_vnic_details=oci_adaptor.oci.core.models.
                    UpdateVnicDetails(nsg_ids=[nsg_id],
                                      skip_source_dest_check=False),
                )

        # pylint: disable=line-too-long
        list_nsg_rules_resp = net_client.list_network_security_group_security_rules(
            network_security_group_id=nsg_id,
            direction='INGRESS',
            sort_by='TIMECREATED',
            sort_order='DESC',
        )

        ingress_rules: List = list_nsg_rules_resp.data
        existing_port_ranges: List[str] = []
        for r in ingress_rules:
            if r.tcp_options:
                options_range = r.tcp_options.destination_port_range
                rule_port_range = f'{options_range.min}-{options_range.max}'
                existing_port_ranges.append(rule_port_range)

        new_ports = resources_utils.port_ranges_to_set(ports)
        existing_ports = resources_utils.port_ranges_to_set(
            existing_port_ranges)
        if new_ports.issubset(existing_ports):
            # ports already contains in the existing rules, nothing to add.
            return

        # Determine the ports to be added, without overlapping.
        ports_to_open = new_ports - existing_ports
        port_ranges_to_open = resources_utils.port_set_to_ranges(ports_to_open)

        new_rules = []
        for port_range in port_ranges_to_open:
            port_range_min, port_range_max = cls.get_range_min_max(port_range)
            new_rules.append(
                oci_adaptor.oci.core.models.AddSecurityRuleDetails(
                    direction='INGRESS',
                    protocol='6',
                    is_stateless=False,
                    source=oci_utils.oci_config.VCN_CIDR_INTERNET,
                    source_type='CIDR_BLOCK',
                    tcp_options=oci_adaptor.oci.core.models.TcpOptions(
                        destination_port_range=oci_adaptor.oci.core.models.
                        PortRange(min=port_range_min, max=port_range_max),),
                    description=oci_utils.oci_config.SERVICE_PORT_RULE_TAG,
                ))

        net_client.add_network_security_group_security_rules(
            network_security_group_id=nsg_id,
            add_network_security_group_security_rules_details=oci_adaptor.oci.
            core.models.AddNetworkSecurityGroupSecurityRulesDetails(
                security_rules=new_rules),
        )

    @classmethod
    @debug_enabled(logger)
    def detach_nsg(cls, region: str, inst, nsg_id: Optional[str]) -> None:
        if nsg_id is None:
            return

        vnic = cls.get_instance_primary_vnic(
            region=region,
            inst_info={
                'inst_id': inst.identifier,
                'ad': inst.availability_domain,
                'compartment': inst.compartment_id,
            })

        # Detatch the NSG before removing it.
        oci_adaptor.get_net_client(region, oci_utils.oci_config.get_profile(
        )).update_vnic(
            vnic_id=vnic.id,
            update_vnic_details=oci_adaptor.oci.core.models.UpdateVnicDetails(
                nsg_ids=[], skip_source_dest_check=False),
        )

    @classmethod
    @debug_enabled(logger)
    def remove_cluster_nsg(cls, region: str, cluster_name: str) -> None:
        """ Remove NSG of the cluster """
        net_client = oci_adaptor.get_net_client(
            region, oci_utils.oci_config.get_profile())

        nsg_name = oci_utils.oci_config.NSG_NAME_TEMPLATE.format(
            cluster_name=cluster_name)
        nsg_id = cls.find_nsg(region, nsg_name, create_if_not_exist=False)
        if nsg_id is None:
            return

        # Delete the NSG
        net_client.delete_network_security_group(
            network_security_group_id=nsg_id)


query_helper = QueryHelper()
