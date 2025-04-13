import json
import time

import requests
from rich import print as rp

import sky

st = sky.serve.status('a2')
st = sky.client.sdk.get(st)[0]
rp('Replicas:')
for r in st['replica_info']:
    rp(r['endpoint'], end=' ')
rp()
exit()
rp('External Load Balancers:')
external_lb_info = st['external_lb_info']
rp(external_lb_info)

external_lb_info = [
    {
        'endpoint': 'http://18.119.124.7:8000'
    },
    {
        'endpoint': 'http://13.114.59.147:8000'
    },
    # {
    #     'endpoint': 'http://54.147.216.103:9002'
    # }
]
with open('@temp/result_queue_size_q72_sky_sgl_lock_queue_v2.txt', 'w') as f:
    while True:
        lb2confs = {'time': time.time()}
        for lb in external_lb_info:
            if lb['endpoint'] is None:
                continue
            # print(lb['endpoint'])
            resp = requests.get(lb['endpoint'] + '/conf')
            conf = resp.json()
            raw_queue_size = requests.get(
                lb['endpoint'] + '/raw-queue-size').json()['queue_size']
            conf['raw_queue_size'] = raw_queue_size
            lb2confs[lb['endpoint']] = conf
            # print(conf)
        print(json.dumps(lb2confs), file=f)
        rp(lb2confs)
        time.sleep(1)
