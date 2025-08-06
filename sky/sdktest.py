import sys

import sky
from sky.client import sdk

print(sys.path)

# import sky

request_id = sdk.status()
print(type(request_id))
print(request_id)

# request_id_prefix = request_id[:10]
# print(request_id_prefix)
clusters = sdk.super_get(request_id)
print(type(clusters))
print(clusters)
cluster = clusters[0]
