import argparse

import requests

_REPEAT = 10

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='SkyServe Smoke Test Client')
    parser.add_argument('--endpoint', type=str, required=True)
    parser.add_argument('--replica-num', type=int, required=True)
    parser.add_argument('--replica-ips',
                        type=str,
                        nargs='*',
                        help="All replica ips")
    args = parser.parse_args()

    replica_ips = []
    for r in range(args.replica_num):
        url = f'http://{args.endpoint}/get_ip'
        resp = requests.get(url)
        assert resp.status_code == 200, resp.text
        assert 'ip' in resp.json(), resp.json()
        ip = resp.json()['ip']
        assert ip not in replica_ips
        replica_ips.append(ip)

    assert set(args.replica_ips) == set(replica_ips)

    for i in range(_REPEAT):
        for r in range(args.replica_num):
            url = f'http://{args.endpoint}/get_ip'
            resp = requests.get(url)
            assert resp.status_code == 200, resp.text
            assert 'ip' in resp.json(), resp.json()
            ip = resp.json()['ip']
            assert ip == replica_ips[r]
