import logging
import oci
from sky.skylet.providers.oci.config import oci_conf

logger = logging.getLogger(__name__)

class oci_query_helper:

    # Call Cloud API to try getting the satisfied nodes.
    @classmethod
    def query_instances_by_tags(cls, tag_filters):
        where_clause_tags = ""
        for tag_key in tag_filters:
            if where_clause_tags != "":
                where_clause_tags += ' && '
            tag_value = tag_filters[tag_key]
            where_clause_tags += f"""(freeformTags.key = '{tag_key}' && freeformTags.value = '{tag_value}')"""

        qv_str = f"""query instance resources where {where_clause_tags} && (lifecycleState != 'TERMINATED' 
                && lifecycleState != 'TERMINATING')""" 
        logger.debug(f"* qv_str is {qv_str}")

        qv = oci.resource_search.models.StructuredSearchDetails(
            query = qv_str, type = 'Structured',
            matching_context_type=oci.resource_search.models.SearchDetails.MATCHING_CONTEXT_TYPE_NONE)

        list_instances_response = oci_conf.search_client.search_resources(qv)
        result_set = list_instances_response.data.items
        logger.debug(f"* Query result: {result_set}")

        return result_set
    # end query_instances_by_tags(...)

    @classmethod
    def terminate_instances_by_tags(cls, tag_filters) -> int: 
        insts = cls.query_instances_by_tags(tag_filters)
        fail_count = 0
        for inst in insts:
            inst_id = inst.identifier
            logger.debug(f"* Got instance(to be terminated): {inst_id}")
            
            try:
                oci_conf.core_client.terminate_instance(inst_id)
            except Exception as e:
                logger.error(f"!!! Terminate instance failed: {inst_id}")
                logger.error(f"!!! {str(e)}")
                logger.error(inst)
                fail_count += 1
        
        if fail_count == 0:
            logger.info(f"* terminate_instances_by_tags success: {tag_filters}")
        else:
            logger.warn(f"! Attention: {fail_count} instances in the cluster failed to be terminate!")
    # end terminate_instances_by_tags