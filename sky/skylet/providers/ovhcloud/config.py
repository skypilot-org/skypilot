# def _get_or_create_server_group(client, service_name, group_name, region=None, group_type="affinity"):
#     """Return the requested server group or create it if it doesn't exist
#
#     :param client: the configured ovhcloud group
#     :param group_name: the name of the expected server group"""
#     server_groups = client.get(f'/cloud/project/{service_name}/instance/group')
#     print("sv grp results:", server_groups)
#     targeted_one = list(filter(lambda x: x['name'] == group_name, server_groups))
#     if targeted_one:
#         return targeted_one[0]
#     targeted_one = client.post(f'/cloud/project/{service_name}/instance/group',
#                                name=group_name,
#                                region=region,
#                                type=group_type)
#     return targeted_one