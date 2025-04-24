"""Utils for generating benchmark commands."""
import argparse
import collections
import json
import shlex

from sky.lbbench import utils

describes = [
    'sgl', 'sky_sgl_enhanced', 'sky_pull_pull', 'sky_push_pull', 'sky_push_push'
]
presents = [
    'Baseline', 'Baseline\\n[Pull]', 'Ours\\n[Pull+Pull]', 'Ours\\n[Push+Pull]',
    'Ours\\n[Push+Push]'
]

enabled_systems = [
    0,  # sgl router
    1,  # sgl router enhanced
    2,  # sky pulling in lb, pulling in replica
    3,  # sky pushing in lb, pulling in replica
    4,  # sky pushing in lb, pushing in replica
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--service-names', type=str, nargs='+', required=True)
    parser.add_argument('--exp-name', type=str, required=True)
    parser.add_argument('--extra-args', type=str, default='')
    parser.add_argument('--output-dir', type=str, default='@temp')
    parser.add_argument('--regions', type=str, default=None, nargs='+')
    parser.add_argument('--region-to-args', type=str, default=None)
    args = parser.parse_args()
    sns = args.service_names
    if len(sns) != len(describes):
        raise ValueError(f'Expected {len(describes)} service names for '
                         f'{", ".join(describes)}')
    print(sns)
    all_st = utils.sky_serve_status()
    ct = utils.sky_status()
    sn2st = {s['name']: s for s in all_st}
    for sn in sns:
        if sn not in sn2st:
            raise ValueError(f'Service {sn} not found')
    sky_sgl_enhanced_ip, sgl_ip = None, None
    for c in ct:
        if c['name'] == utils.sgl_cluster:
            sgl_ip = c['handle'].head_ip
        elif c['name'] == utils.sky_sgl_enhanced_cluster:
            sky_sgl_enhanced_ip = c['handle'].head_ip

    endpoints = [
        f'{sgl_ip}:9001', f'{sky_sgl_enhanced_ip}:9002',
        sn2st[sns[2]]['endpoint'], sn2st[sns[3]]['endpoint'],
        sn2st[sns[4]]['endpoint']
    ]
    print(endpoints)
    name_mapping = []
    ens = []
    scps = []
    exp2backend = {}
    cn2cmds = collections.defaultdict(list)

    regions = None
    if args.regions is not None and args.region_to_args is not None:
        raise ValueError('--regions and --region-to-args cannot both be set')
    if args.regions is not None:
        regions = args.regions
    elif args.region_to_args is not None:
        regions = json.loads(args.region_to_args)

    output = '~'
    output_local = args.output_dir

    for i, (e, d, p) in enumerate(zip(endpoints, describes, presents)):
        if i not in enabled_systems:
            continue
        en = f'{args.exp_name}_{d}'
        ens.append(en)
        name_mapping.append(f'    \'{en}\': \'{p}\',')
        cmd = (
            f'python3 -m sky.lbbench.bench --exp-name {en} --backend-url {e} '
            f'{args.extra_args}')
        if regions is None:
            print(cmd)
        else:
            exp2backend[en] = e
            cmd += f' --output-dir {output} -y'
            scps.append(f'mkdir -p {output_local}/result/metric/{en}')
            for r in regions:
                cluster = f'llmc-{r}'
                region_cmd = f'{cmd} --seed {r}'
                if isinstance(regions, dict):
                    region_cmd += f' {regions[r]}'
                # TODO(tian): Instead of --fast, how about sky launch once
                # and then sky exec?
                cn2cmds[cluster].append(
                    f'sky launch --region {r} -c {cluster} --detach-run -y '
                    f'--fast --env CMD={shlex.quote(region_cmd)} '
                    '--env HF_TOKEN examples/serve/external-lb/client.yaml')
                output_remote = f'{cluster}:{output}/result'
                met = f'{output_remote}/metric/{en}.json'
                scps.append(f'scp {met} {output_local}/result'
                            f'/metric/{en}/{cluster}.json')
    print(f'{"Queue status puller (Running locally)":=^70}')
    print(f'python3 -m sky.lbbench.queue_fetcher '
          f'--exp2backend {shlex.quote(json.dumps(exp2backend))} '
          f'--output-dir {output_local}')
    print(f'{"Launch Clients":=^70}')
    # Use this order so that the overhead to launch the cluster is aligned.
    for _, cmds in cn2cmds.items():
        for c in cmds:
            print(c)
        print('*' * 30)
    print(f'{"Sync down results":=^70}')
    for s in scps:
        print(s)
    print(f'{"Generate result table":=^70}')
    for nm in name_mapping:
        print(nm)


if __name__ == '__main__':
    # py -m sky.lbbench.gen_cmd --service-names b1 b2 b3 --exp-name arena_syn_r3_c2000_u250_d240 --extra-args '--workload arena_syn --duration 240 --num-conv 2000 --num-users 250' # pylint: disable=line-too-long
    # py -m sky.lbbench.gen_cmd --service-names d1 d2 d3 --exp-name arena_syn_mrc_tail_c2000_u300_d240 --extra-args '--workload arena_syn --duration 240 --num-conv 2000 --num-users 150' --region-to-users '{"us-east-2":200,"ap-northeast-1"}'
    main()
