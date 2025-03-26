import requests
from rich import print

import sky

st = sky.serve.status('skysgl')
st = sky.client.sdk.get(st)[0]
# print('Replicas:')
# for r in st['replica_info']:
#     print(r['endpoint'], end=' ')
# print()
print('External Load Balancers:')
for lb in st['external_lb_info']:
    if lb['endpoint'] is None:
        continue
    print(lb['endpoint'])
    resp = requests.get(lb['endpoint'] + '/conf')
    conf = resp.json()
    raw_queue_size = requests.get(lb['endpoint'] +
                                  '/raw-queue-size').json()['queue_size']
    conf['raw_queue_size'] = raw_queue_size
    print(conf)
print()
