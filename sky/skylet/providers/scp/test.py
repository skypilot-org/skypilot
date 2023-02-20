from sky.skylet.providers.scp.scp_utils import SCPClient
# print("hello world")
scp_client = SCPClient()
instance_type = 'gpu_1x_a100_sxm4'
region = 'us-east-1'
quantity = 1
name = 'skypilottest001'
# scp_client.create_instances(instance_type=instance_type,region=region,quantity=quantity,name=name)
res = scp_client.list_instances()
# res = scp_client.create_instances()
print(res)