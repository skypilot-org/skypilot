"""Utils for generating benchmark commands."""
import argparse
import collections
import shlex

from sky.lbbench import utils

describes = ['sgl', 'sky_sgl_enhanced', 'sky']
presents = ['Baseline', 'Baseline\\n[Enhanced]', 'Ours']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--service-names', type=str, nargs='+', required=True)
    parser.add_argument('--exp-name', type=str, required=True)
    parser.add_argument('--extra-args', type=str, default='')
    parser.add_argument('--output-dir', type=str, default='@temp')
    parser.add_argument('--regions', type=str, default=None, nargs='+')
    args = parser.parse_args()
    sns = args.service_names
    if len(sns) != 3:
        raise ValueError('Expected 3 service names for '
                         'sky, sky-sgl-enhanced, sgl')
    print(sns)
    all_st = utils.sky_serve_status()
    ct = utils.sky_status()
    sn2st = {s['name']: s for s in all_st}
    for sn in sns:
        if sn not in sn2st:
            raise ValueError(f'Service {sn} not found')
    sky_sgl_enhanced_ip, sgl_ip = None, None
    for c in ct:
        if c['name'] == utils.sky_sgl_enhanced_cluster:
            sky_sgl_enhanced_ip = c['handle'].head_ip
        elif c['name'] == utils.sgl_cluster:
            sgl_ip = c['handle'].head_ip

    endpoints = [
        f'{sgl_ip}:9001', f'{sky_sgl_enhanced_ip}:9002',
        sn2st[sns[0]]['endpoint']
    ]
    print(endpoints)
    name_mapping = []
    ens = []
    scps = []
    cmd_run_locally = []
    cn2cmds = collections.defaultdict(list)
    print('\n\n')
    for e, d, p in zip(endpoints, describes, presents):
        en = f'{args.exp_name}_{d}'
        ens.append(en)
        name_mapping.append(f'    \'{en}\': \'{p}\',')
        cmd = (
            f'python3 -m sky.lbbench.bench --exp-name {en} --backend-url {e} '
            f'{args.extra_args}')
        if args.regions is None:
            print(cmd)
        else:
            cmd_run_locally.append(f'{cmd} --skip-tasks')
            output = '~'
            output_local = args.output_dir
            cmd += f' --skip-queue-status --output-dir {output} -y'
            scps.append(f'mkdir -p {output_local}/result/metric/{en}')
            # scps.append(f'mkdir -p {output_local}/result/queue_size/{en}')
            for r in args.regions:
                cluster = f'llmc-{r}'
                cn2cmds[cluster].append(
                    f'sky launch --region {r} -c {cluster} --detach-run -y '
                    f'--env CMD={shlex.quote(cmd)} --env HF_TOKEN '
                    'examples/serve/external-lb/client.yaml')
                output_remote = f'{cluster}:{output}/result'
                met = f'{output_remote}/metric/{en}.json'
                scps.append(f'scp {met} {output_local}/result'
                            f'/metric/{en}/{cluster}.json')
                # qs = f'{output_remote}/queue_size/{en}.txt'
                # scps.append(
                #     f'scp {qs} {output}/result/queue_size/{en}/{cluster}.txt')
            print()
    print('=' * 30)
    for c in cmd_run_locally:
        print(c)
        print()
    print('=' * 30)
    # Use this order so that the overhead to launch the cluster is aligned.
    for _, cmds in cn2cmds.items():
        for c in cmds:
            print(c)
        print()
    print('=' * 30)
    for s in scps:
        print(s)
    print('=' * 30)
    for en in ens:
        print(f'    \'{en}\',')
    print('=' * 30)
    for nm in name_mapping:
        print(nm)


if __name__ == '__main__':
    # py -m sky.lbbench.gen_cmd --service-names b1 b2 b3 --exp-name arena_syn_r3_c2000_u250_d240 --extra-args '--workload arena_syn --duration 240 --num-conv 2000 --num-users 250' # pylint: disable=line-too-long
    main()
