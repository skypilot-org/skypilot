import json
import time

import requests
from rich import print as rp

import sky

# st = sky.serve.status('llmtest4')
# st = sky.client.sdk.get(st)[0]
# rp('Replicas:')
# for r in st['replica_info']:
#     rp(r['endpoint'], end=' ')
# rp()
# rp('External Load Balancers:')
# external_lb_info = st['external_lb_info']
# rp(external_lb_info)
# exit()

external_lb_info = [
    {
        'endpoint': 'http://18.217.237.196:8000'
    },
    {
        'endpoint': 'http://54.249.171.175:8000'
    },
]
with open('@temp/result/queue_size_q72_sky.txt', 'w') as f:
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
