# Benchmark Steps

## Step 0: Prerequisites

Installing skypilot from source & checkout the `serve-multi-region-lb` branch:

```bash
git clone git@github.com:skypilot-org/skypilot.git
cd skypilot
git switch serve-multi-region-lb
pip install -e '.[aws]'
sky check aws
```

If you have a previous installation, restart the api server and cleanup any wheel cache:

```bash
sky api stop
sky api start
rm -rf ~/.sky/.wheels_lock ~/.sky/wheels
sky down sky-serve-controller-<user-hash>
```

Cloning the plot script to the correct path:

```bash
$ pwd
/path/to/skypilot  # clone the plot repo under skypilot directory
$ mkdir @temp && cd @temp
$ git clone git@github.com:cblmemo/sky-lb-e2e-eval-result.git result
```

> The `@temp` dir is the default argument for all benchmark scripts. It should also be possible to change it by `--output-dir`.

Prepare your Hugging Face Token:

```bash
export HF_TOKEN=<your-huggingface-token>
```

This token should have access to `meta-llama/Llama-3.1-8B-Instruct` and `lmsys/chatbot_arena_conversations`.

## Step 1: Launch Services

Adjusting the service YAML (`examples/serve/external-lb/llm.yaml`) based on desired replica configuration. The default is 2 replicas in `us-east-2` and 2 replicas in `ap-northeast-1`. **All replicas will be launched in a round-robin fashion in the `ordered` region list**. e.g. if there is 3 regions and 4 replicas, the first region in the list will have 2 replicas and the other two regions will have 1 replica each. **All replicas should use AWS cloud for now**.

When adding replicas to other regions, make sure to update the `external_load_balancers` section to add one load balancer for the new region. **All load balancers should use AWS cloud**. The `route53_hosted_zone` should be configured in the given credentials and no changes is needed - if you need to add a new one, please contact the author.

Running the following command for 4 times.

- `svc1`: SGLang Router
- `svc2`: SGLang Router [Pull]
- `svc3`: LB Pull (V1 stealing) + Replica Pull
- `svc4`: LB Pull (V2 stealing) + Replica Pull
- `svc5`: LB Push + Replica Pull
- `svc6`: LB Push + Replica Push
- `svc7`: LB Pull + Replica Pull, but Pull by selective pushing instead of stealing
- There is no LB Pull + Replica Push since no request will left in replica. Hence no request left in LB to pull.

You can launch all of them at once by:

```bash
sky serve up examples/serve/external-lb/llm.yaml -y -n svc1 --env HF_TOKEN
sky serve up examples/serve/external-lb/llm.yaml -y -n svc2 --env HF_TOKEN
sky serve up examples/serve/external-lb/llm.yaml -y -n svc3 --env HF_TOKEN
sky serve up examples/serve/external-lb/llm.yaml -y -n svc4 --env HF_TOKEN --env USE_V2_STEALING=true
sky serve up examples/serve/external-lb/llm.yaml -y -n svc5 --env HF_TOKEN --env DO_PUSHING_ACROSS_LB=true
sky serve up examples/serve/external-lb/llm.yaml -y -n svc6 --env HF_TOKEN --env DO_PUSHING_ACROSS_LB=true --env DO_PUSHING_TO_REPLICA=true
sky serve up examples/serve/external-lb/llm.yaml -y -n svc7 --env HF_TOKEN --env DO_PUSHING_ACROSS_LB=true --env LB_PUSHING_ENABLE_LB=false
```

Here is a easy-to-use script:

```bash
PREFIX="svc" sky/lbbench/launch_systems.sh
```

Keep running `sky serve status -v` until all of them are ready (all replicas are ready):

```bash
$ sky serve status -v
Services
NAME  VERSION  UPTIME   STATUS  REPLICAS  EXTERNAL_LBS  ENDPOINT                   AUTOSCALING_POLICY  LOAD_BALANCING_POLICY  REQUESTED_RESOURCES  
svc5  1        20m 12s  READY   4/4       2/2           svc5.aws.cblmemo.net:8000  Fixed 4 replicas    prefix_tree            1x[L4:1]             
svc1  1        20m 40s  READY   4/4       2/2           svc1.aws.cblmemo.net:8000  Fixed 4 replicas    prefix_tree            1x[L4:1]             
svc3  1        20m 20s  READY   4/4       2/2           svc3.aws.cblmemo.net:8000  Fixed 4 replicas    prefix_tree            1x[L4:1]             
svc4  1        20m 36s  READY   4/4       2/2           svc4.aws.cblmemo.net:8000  Fixed 4 replicas    prefix_tree            1x[L4:1]             
svc2  1        20m 45s  READY   4/4       2/2           svc2.aws.cblmemo.net:8000  Fixed 4 replicas    prefix_tree            1x[L4:1]             

Service Replicas
SERVICE_NAME  ID  VERSION  ENDPOINT                    LAUNCHED     RESOURCES                                                      STATUS  REGION          ZONE             
svc5          1   1        http://43.207.115.174:8081  1 min ago    1x AWS(g6.4xlarge, {'L4': 1}, disk_tier=high, ports=['8081'])  READY   ap-northeast-1  ap-northeast-1a  
svc5          2   1        http://18.119.111.119:8081  21 mins ago  1x AWS(g6.4xlarge, {'L4': 1}, disk_tier=high, ports=['8081'])  READY   us-east-2       us-east-2a       
svc5          3   1        http://18.183.57.17:8081    1 min ago    1x AWS(g6.4xlarge, {'L4': 1}, disk_tier=high, ports=['8081'])  READY   ap-northeast-1  ap-northeast-1c  
svc5          4   1        http://3.15.150.242:8081    21 mins ago  1x AWS(g6.4xlarge, {'L4': 1}, disk_tier=high, ports=['8081'])  READY   us-east-2       us-east-2a       
svc1          1   1        http://54.178.80.208:8081   21 mins ago  1x AWS(g6.4xlarge, {'L4': 1}, disk_tier=high, ports=['8081'])  READY   ap-northeast-1  ap-northeast-1a  
svc1          2   1        http://3.137.168.179:8081   22 mins ago  1x AWS(g6.4xlarge, {'L4': 1}, disk_tier=high, ports=['8081'])  READY   us-east-2       us-east-2a       
svc1          3   1        http://43.207.108.244:8081  21 mins ago  1x AWS(g6.4xlarge, {'L4': 1}, disk_tier=high, ports=['8081'])  READY   ap-northeast-1  ap-northeast-1a  
svc1          4   1        http://3.144.37.203:8081    22 mins ago  1x AWS(g6.4xlarge, {'L4': 1}, disk_tier=high, ports=['8081'])  READY   us-east-2       us-east-2a       
svc3          1   1        http://18.183.179.168:8081  2 mins ago   1x AWS(g6.4xlarge, {'L4': 1}, disk_tier=high, ports=['8081'])  READY   ap-northeast-1  ap-northeast-1a  
svc3          2   1        http://18.116.42.193:8081   21 mins ago  1x AWS(g6.4xlarge, {'L4': 1}, disk_tier=high, ports=['8081'])  READY   us-east-2       us-east-2a       
svc3          3   1        http://43.206.151.166:8081  3 mins ago   1x AWS(g6.4xlarge, {'L4': 1}, disk_tier=high, ports=['8081'])  READY   ap-northeast-1  ap-northeast-1c  
svc3          4   1        http://18.218.48.79:8081    21 mins ago  1x AWS(g6.4xlarge, {'L4': 1}, disk_tier=high, ports=['8081'])  READY   us-east-2       us-east-2a       
svc4          1   1        http://18.181.198.138:8081  3 mins ago   1x AWS(g6.4xlarge, {'L4': 1}, disk_tier=high, ports=['8081'])  READY   ap-northeast-1  ap-northeast-1a  
svc4          2   1        http://18.118.37.42:8081    21 mins ago  1x AWS(g6.4xlarge, {'L4': 1}, disk_tier=high, ports=['8081'])  READY   us-east-2       us-east-2a       
svc4          3   1        http://43.207.182.196:8081  3 mins ago   1x AWS(g6.4xlarge, {'L4': 1}, disk_tier=high, ports=['8081'])  READY   ap-northeast-1  ap-northeast-1c  
svc4          4   1        http://3.137.166.123:8081   21 mins ago  1x AWS(g6.4xlarge, {'L4': 1}, disk_tier=high, ports=['8081'])  READY   us-east-2       us-east-2a       
svc2          1   1        http://18.179.9.124:8081    21 mins ago  1x AWS(g6.4xlarge, {'L4': 1}, disk_tier=high, ports=['8081'])  READY   ap-northeast-1  ap-northeast-1a  
svc2          2   1        http://3.128.31.100:8081    22 mins ago  1x AWS(g6.4xlarge, {'L4': 1}, disk_tier=high, ports=['8081'])  READY   us-east-2       us-east-2a       
svc2          3   1        http://52.193.151.185:8081  21 mins ago  1x AWS(g6.4xlarge, {'L4': 1}, disk_tier=high, ports=['8081'])  READY   ap-northeast-1  ap-northeast-1a  
svc2          4   1        http://3.15.229.172:8081    22 mins ago  1x AWS(g6.4xlarge, {'L4': 1}, disk_tier=high, ports=['8081'])  READY   us-east-2       us-east-2a       

External Load Balancers
SERVICE_NAME  ID  VERSION  ENDPOINT                    LAUNCHED     RESOURCES                          STATUS  REGION          ZONE             
svc5          1   1        http://18.191.178.226:8000  21 mins ago  1x AWS(m6i.large, ports=['8000'])  READY   us-east-2       us-east-2a       
svc5          2   1        http://52.195.182.20:8000   21 mins ago  1x AWS(m6i.large, ports=['8000'])  READY   ap-northeast-1  ap-northeast-1a  
svc1          1   1        http://18.216.134.208:8000  22 mins ago  1x AWS(m6i.large, ports=['8000'])  READY   us-east-2       us-east-2a       
svc1          2   1        http://13.231.55.224:8000   21 mins ago  1x AWS(m6i.large, ports=['8000'])  READY   ap-northeast-1  ap-northeast-1a  
svc3          1   1        http://18.217.140.173:8000  21 mins ago  1x AWS(m6i.large, ports=['8000'])  READY   us-east-2       us-east-2a       
svc3          2   1        http://13.231.3.176:8000    21 mins ago  1x AWS(m6i.large, ports=['8000'])  READY   ap-northeast-1  ap-northeast-1a  
svc4          1   1        http://18.118.207.237:8000  21 mins ago  1x AWS(m6i.large, ports=['8000'])  READY   us-east-2       us-east-2a       
svc4          2   1        http://52.194.190.7:8000    21 mins ago  1x AWS(m6i.large, ports=['8000'])  READY   ap-northeast-1  ap-northeast-1a  
svc2          1   1        http://18.119.116.201:8000  22 mins ago  1x AWS(m6i.large, ports=['8000'])  READY   us-east-2       us-east-2a       
svc2          2   1        http://3.112.123.186:8000   21 mins ago  1x AWS(m6i.large, ports=['8000'])  READY   ap-northeast-1  ap-northeast-1a 
```

## Step 2: Launch baseline load balancers

We compare the performance of our load balancer with the following baselines:

- SGLang Router
- SGLang Router with Rate Limiting

The following util script will launch the baseline load balancers for the given service names. **The order of the service names matters here**. The first will be used as SGLang Router and the second will be used as SGLang Router with Rate Limiting.

```bash
python3 -m sky.lbbench.launch_lb --service-names svc1 svc2
```

Press enter to confirm and launch the load balancers. After the script exits, run the following command to check the status of the load balancers. You should see the following output:

```bash
$ sky logs sgl-router
...
(task, pid=2024) [Router (Rust)] 2025-04-24 06:29:08 - INFO - All workers are healthy
(task, pid=2024) [Router (Rust)] 2025-04-24 06:29:08 - INFO - ✅ Serving router on 0.0.0.0:9001
(task, pid=2024) [Router (Rust)] 2025-04-24 06:29:08 - INFO - ✅ Serving workers on ["http://54.178.80.208:8081", "http://3.137.168.179:8081", "http://43.207.108.244:8081", "http://3.144.37.203:8081"]
```

```bash
$ sky logs sgl-router-pull
(load-balancer, pid=2007) INFO:__main__:All ready LB URLs: {'us-east-2': ['http://18.119.116.201:8000'], 'ap-northeast-1': ['http://3.112.123.186:8000']}
(load-balancer, pid=2007) INFO:__main__:Available Replica URLs: {'ap-northeast-1': ['http://18.179.9.124:8081', 'http://52.193.151.185:8081'], 'us-east-2': ['http://3.128.31.100:8081', 'http://3.15.229.172:8081']}, Ready URLs in local region global: ['http://18.179.9.124:8081', 'http://52.193.151.185:8081', 'http://3.128.31.100:8081', 'http://3.15.229.172:8081']
```

Make sure each load balancer has the desired number of replicas.

## Step 3: Generate Bash Scripts to Use

We have a util script to generate the benchmark commands. This doc will only cover the usage of multi-region clients, which means the requests will be simultaneously sent from multiple regions.

**Notice that the service names should be the same order as the ones used in Step 2**.

Explanation of the arguments:

- `--exp-name`: Identifier for the experiment. Please describe the experiment config in the name.
- `--extra-args`: Workload specific arguments.
- `--regions`: Client regions. This should be a list.

**Notice that the `--extra-args` will be applied to all regions**. If you want a total concurrency of 300, you should set `--num-users (300 / num-regions)` for each region.

```bash
python3 -m sky.lbbench.gen_cmd --service-names svc1 svc2 svc3 svc4 svc5 \
  --exp-name arena_syn_mrc_tail_c2000_u300_d240 \
  --extra-args '--workload arena_syn --duration 240 --num-conv 2000 --num-users 150' \
  --regions us-east-2 ap-northeast-1
```

### Side Note: Support for different configurations in different regions

For testing config that different regions have different configurations, you can use `--region-to-args`. This should be a json string. e.g.

```bash
python3 -m sky.lbbench.gen_cmd --service-names svc1 svc2 svc3 svc4 svc5 \
  --exp-name arena_syn_mrc_100_50_tail_c2000_u150_d240 \
  --extra-args '--workload arena_syn --duration 240 --num-conv 2000' \
  --region-to-args '{"us-east-2":"--num-users 100","ap-northeast-1":"--num-users 50"}'
```

One and only one of the `--regions` and `--region-to-args` should be set. If `--region-to-args` is set, the keys will be used. Remember to remove any arguments from `--extra-args` that are already specified in `--region-to-args`.

You should see the following output:

```bash
======================Parallel execution script=======================
Generated parallel execution script at @temp/result/scripts/arena_syn_mrc_100_50_tail_c2000_u150_d240.bash
Run with: bash @temp/result/scripts/arena_syn_mrc_100_50_tail_c2000_u150_d240.bash
```

## Step 4: Run the commands

A script will be generated at `@temp/result/scripts/`. Run it with:

```bash
bash @temp/result/scripts/arena_syn_mrc_100_50_tail_c2000_u150_d240.bash
```

And wait for it to finish.

## Step 5: Plot

Final step is to plot the results. You should see the following output from the gen cmd script:

```bash
========================Generate result table=========================
    'arena_syn_mrc_100_50_tail_c2000_u150_d240_sgl': 'Baseline',
    'arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_sgl_enhanced': 'Baseline\n[Pull]',
    'arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_pull_pull': 'Ours\n[Pull+Pull]',
    'arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_push_pull': 'Ours\n[Push+Pull]',
    'arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_push_push': 'Ours\n[Push+Push]',
```

Copy-pasting them into the `gn2alias` variable in the `@temp/result/plot.py` script and run it. **Make sure to comment out other parts in the variable**.

```bash
python3 @temp/result/plot.py
```

You should see the figures in the `@temp/result/fig` directory.

## Step 6: Cleanup

**Notice: please terminate all the clusters after use**.

```bash
# Terminate all services
sky serve down -ay

# Stop all LBs and clients
sky stop -ay
# Or only cancel it if you want to keep using them in the next run
sky cancel -ay sgl-router && sky cancel -ay sgl-router-pull
```

> The following content is stale. Don't need to check it.

## [Stale] Step 6: Run the commands

You will need a lot of terminals to run the commands. Specifically, #regions + 1 (2 + 1 in the default configurations). There are 3 types of commands:

- Queue status puller (running locally): pull the queue status from the load balancers and save them to the local directory.
- Launch clients: launch the clients in the given regions.
- Sync down results: sync down the results from the load balancers to the local directory.

### Queue status puller

You will see 1 command. It will pull status from all systems.

```bash
================Queue status puller (Running locally)=================
python3 -m sky.lbbench.queue_fetcher --exp2backend '{"arena_syn_mrc_100_50_tail_c2000_u150_d240_sgl": "44.202.52.238:9001", "arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_sgl_enhanced": "52.91.17.83:9002", "arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_pull_pull": "svc3.aws.cblmemo.net:8000", "arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_push_pull": "svc4.aws.cblmemo.net:8000", "arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_push_push": "svc5.aws.cblmemo.net:8000"}' --output-dir @temp
```

**Press enter to confirm after running it**. You should see the following output after confirmation:

```bash
Pulling queue status:      tail -f /var/folders/bt/ptxj0qbj6698tysr2_z614_r0000gn/T/result_queue_size_arena_syn_mrc_100_50_tail_c2000_u150_d240_sgl.txt | jq
Pulling queue status:      tail -f /var/folders/bt/ptxj0qbj6698tysr2_z614_r0000gn/T/result_queue_size_arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_sgl_enhanced.txt | jq
Pulling queue status:      tail -f /var/folders/bt/ptxj0qbj6698tysr2_z614_r0000gn/T/result_queue_size_arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_pull_pull.txt | jq
Pulling queue status:      tail -f /var/folders/bt/ptxj0qbj6698tysr2_z614_r0000gn/T/result_queue_size_arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_push_pull.txt | jq
Pulling queue status:      tail -f /var/folders/bt/ptxj0qbj6698tysr2_z614_r0000gn/T/result_queue_size_arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_push_push.txt | jq
```

Keep them running until the experiment finishes.

### Launch clients

You will see #regions lines of commands. Each line will launch clients in one region. They are separated by 30 `*`s.

```bash
============================Launch Clients============================
sky launch --region us-east-2 -c llmc-us-east-2 --detach-run -y --fast --env CMD='python3 -m sky.lbbench.bench --exp-name arena_syn_mrc_100_50_tail_c2000_u150_d240_sgl --backend-url 44.202.52.238:9001 --workload arena_syn --duration 240 --output-dir ~ -y --seed us-east-2 --num-users 100' --env HF_TOKEN examples/serve/external-lb/client.yaml
sky launch --region us-east-2 -c llmc-us-east-2 --detach-run -y --fast --env CMD='python3 -m sky.lbbench.bench --exp-name arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_sgl_enhanced --backend-url 52.91.17.83:9002 --workload arena_syn --duration 240 --output-dir ~ -y --seed us-east-2 --num-users 100' --env HF_TOKEN examples/serve/external-lb/client.yaml
sky launch --region us-east-2 -c llmc-us-east-2 --detach-run -y --fast --env CMD='python3 -m sky.lbbench.bench --exp-name arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_pull_pull --backend-url svc3.aws.cblmemo.net:8000 --workload arena_syn --duration 240 --output-dir ~ -y --seed us-east-2 --num-users 100' --env HF_TOKEN examples/serve/external-lb/client.yaml
sky launch --region us-east-2 -c llmc-us-east-2 --detach-run -y --fast --env CMD='python3 -m sky.lbbench.bench --exp-name arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_push_pull --backend-url svc4.aws.cblmemo.net:8000 --workload arena_syn --duration 240 --output-dir ~ -y --seed us-east-2 --num-users 100' --env HF_TOKEN examples/serve/external-lb/client.yaml
sky launch --region us-east-2 -c llmc-us-east-2 --detach-run -y --fast --env CMD='python3 -m sky.lbbench.bench --exp-name arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_push_push --backend-url svc5.aws.cblmemo.net:8000 --workload arena_syn --duration 240 --output-dir ~ -y --seed us-east-2 --num-users 100' --env HF_TOKEN examples/serve/external-lb/client.yaml
******************************
sky launch --region ap-northeast-1 -c llmc-ap-northeast-1 --detach-run -y --fast --env CMD='python3 -m sky.lbbench.bench --exp-name arena_syn_mrc_100_50_tail_c2000_u150_d240_sgl --backend-url 44.202.52.238:9001 --workload arena_syn --duration 240 --output-dir ~ -y --seed ap-northeast-1 --num-users 50' --env HF_TOKEN examples/serve/external-lb/client.yaml
sky launch --region ap-northeast-1 -c llmc-ap-northeast-1 --detach-run -y --fast --env CMD='python3 -m sky.lbbench.bench --exp-name arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_sgl_enhanced --backend-url 52.91.17.83:9002 --workload arena_syn --duration 240 --output-dir ~ -y --seed ap-northeast-1 --num-users 50' --env HF_TOKEN examples/serve/external-lb/client.yaml
sky launch --region ap-northeast-1 -c llmc-ap-northeast-1 --detach-run -y --fast --env CMD='python3 -m sky.lbbench.bench --exp-name arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_pull_pull --backend-url svc3.aws.cblmemo.net:8000 --workload arena_syn --duration 240 --output-dir ~ -y --seed ap-northeast-1 --num-users 50' --env HF_TOKEN examples/serve/external-lb/client.yaml
sky launch --region ap-northeast-1 -c llmc-ap-northeast-1 --detach-run -y --fast --env CMD='python3 -m sky.lbbench.bench --exp-name arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_push_pull --backend-url svc4.aws.cblmemo.net:8000 --workload arena_syn --duration 240 --output-dir ~ -y --seed ap-northeast-1 --num-users 50' --env HF_TOKEN examples/serve/external-lb/client.yaml
sky launch --region ap-northeast-1 -c llmc-ap-northeast-1 --detach-run -y --fast --env CMD='python3 -m sky.lbbench.bench --exp-name arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_push_push --backend-url svc5.aws.cblmemo.net:8000 --workload arena_syn --duration 240 --output-dir ~ -y --seed ap-northeast-1 --num-users 50' --env HF_TOKEN examples/serve/external-lb/client.yaml
******************************
```

Running each of them in a separate terminal. They will **sequentially** launch the clients for all experiment in the same order and exit after the experiment is scheduled.

**You can just copy-pasting each group of them and press enter all at once**. They will be executed sequentially. You should see **#systems output like this for each command**:

```bash
📋 Useful Commands
Job ID: 5
├── To cancel the job:          sky cancel llmc-us-east-2 5
├── To stream job logs:         sky logs llmc-us-east-2 5
└── To view job queue:          sky queue llmc-us-east-2
Cluster name: llmc-us-east-2
├── To log into the head VM:    ssh llmc-us-east-2
├── To submit a job:            sky exec llmc-us-east-2 yaml_file
├── To stop the cluster:        sky stop llmc-us-east-2
└── To teardown the cluster:    sky down llmc-us-east-2
```

Monitor the job status until all of them are completed. It will shows `RUNNING` first:

```bash
$ sky queue llmc-ap-northeast-1 llmc-us-east-2 | grep RUNNING 
5   -     tianxia  53 secs ago  51 secs ago  51s       1x[CPU:8+]  RUNNING  ~/sky_logs/sky-2025-04-23-23-38-03-734798  
4   -     tianxia  57 secs ago  55 secs ago  55s       1x[CPU:8+]  RUNNING  ~/sky_logs/sky-2025-04-23-23-38-00-229789  
3   -     tianxia  1 min ago    58 secs ago  58s       1x[CPU:8+]  RUNNING  ~/sky_logs/sky-2025-04-23-23-37-56-993378  
2   -     tianxia  1 min ago    1 min ago    1m 2s     1x[CPU:8+]  RUNNING  ~/sky_logs/sky-2025-04-23-23-37-30-011096  
1   -     tianxia  1 min ago    1 min ago    1m 21s    1x[CPU:8+]  RUNNING  ~/sky_logs/sky-2025-04-23-23-36-33-392323  
5   -     tianxia  29 secs ago  27 secs ago  27s       1x[CPU:8+]  RUNNING  ~/sky_logs/sky-2025-04-23-23-38-28-474533  
4   -     tianxia  33 secs ago  30 secs ago  30s       1x[CPU:8+]  RUNNING  ~/sky_logs/sky-2025-04-23-23-38-23-955212  
3   -     tianxia  37 secs ago  35 secs ago  35s       1x[CPU:8+]  RUNNING  ~/sky_logs/sky-2025-04-23-23-38-20-019992  
2   -     tianxia  41 secs ago  39 secs ago  39s       1x[CPU:8+]  RUNNING  ~/sky_logs/sky-2025-04-23-23-37-40-523381  
1   -     tianxia  1 min ago    1 min ago    1m 11s    1x[CPU:8+]  RUNNING  ~/sky_logs/sky-2025-04-23-23-36-31-962455  
```

Keep running the same command and wait until it reaches `SUCCEEDED`. This should end in the duration you specified in the `--extra-args`.

```bash
$ sky queue llmc-ap-northeast-1 llmc-us-east-2
Fetching and parsing job queue...
Fetching job queue for: llmc-ap-northeast-1, llmc-us-east-2

Job queue of current user on cluster llmc-us-east-2
ID  NAME  USER     SUBMITTED    STARTED      DURATION  RESOURCES   STATUS     LOG                                        
5   -     tianxia  10 mins ago  10 mins ago  4m 29s    1x[CPU:8+]  SUCCEEDED  ~/sky_logs/sky-2025-04-23-23-38-03-734798  
4   -     tianxia  10 mins ago  10 mins ago  4m 29s    1x[CPU:8+]  SUCCEEDED  ~/sky_logs/sky-2025-04-23-23-38-00-229789  
3   -     tianxia  10 mins ago  10 mins ago  4m 30s    1x[CPU:8+]  SUCCEEDED  ~/sky_logs/sky-2025-04-23-23-37-56-993378  
2   -     tianxia  10 mins ago  10 mins ago  4m 29s    1x[CPU:8+]  SUCCEEDED  ~/sky_logs/sky-2025-04-23-23-37-30-011096  
1   -     tianxia  11 mins ago  11 mins ago  4m 36s    1x[CPU:8+]  SUCCEEDED  ~/sky_logs/sky-2025-04-23-23-36-33-392323  

Job queue of current user on cluster llmc-ap-northeast-1
ID  NAME  USER     SUBMITTED    STARTED      DURATION  RESOURCES   STATUS     LOG                                        
5   -     tianxia  10 mins ago  10 mins ago  4m 28s    1x[CPU:8+]  SUCCEEDED  ~/sky_logs/sky-2025-04-23-23-38-28-474533  
4   -     tianxia  10 mins ago  10 mins ago  4m 26s    1x[CPU:8+]  SUCCEEDED  ~/sky_logs/sky-2025-04-23-23-38-23-955212  
3   -     tianxia  10 mins ago  10 mins ago  4m 27s    1x[CPU:8+]  SUCCEEDED  ~/sky_logs/sky-2025-04-23-23-38-20-019992  
2   -     tianxia  10 mins ago  10 mins ago  4m 26s    1x[CPU:8+]  SUCCEEDED  ~/sky_logs/sky-2025-04-23-23-37-40-523381  
1   -     tianxia  11 mins ago  11 mins ago  4m 29s    1x[CPU:8+]  SUCCEEDED  ~/sky_logs/sky-2025-04-23-23-36-31-962455 
```

**If it does not ends with status `SUCCEEDED`, you can check the logs for more details**.

```bash
$ sky logs llmc-us-east-2 5
```

### Sync down results

You will see a group of commands. Each line will sync down the results from one client on one system.

**You can just copy-pasting all of them and press enter all at once**. Check for any unusual error output like file not found.

```bash
==========================Sync down results===========================
mkdir -p @temp/result/metric/arena_syn_mrc_100_50_tail_c2000_u150_d240_sgl
scp llmc-us-east-2:~/result/metric/arena_syn_mrc_100_50_tail_c2000_u150_d240_sgl.json @temp/result/metric/arena_syn_mrc_100_50_tail_c2000_u150_d240_sgl/llmc-us-east-2.json
scp llmc-ap-northeast-1:~/result/metric/arena_syn_mrc_100_50_tail_c2000_u150_d240_sgl.json @temp/result/metric/arena_syn_mrc_100_50_tail_c2000_u150_d240_sgl/llmc-ap-northeast-1.json
mkdir -p @temp/result/metric/arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_sgl_enhanced
scp llmc-us-east-2:~/result/metric/arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_sgl_enhanced.json @temp/result/metric/arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_sgl_enhanced/llmc-us-east-2.json
scp llmc-ap-northeast-1:~/result/metric/arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_sgl_enhanced.json @temp/result/metric/arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_sgl_enhanced/llmc-ap-northeast-1.json
mkdir -p @temp/result/metric/arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_pull_pull
scp llmc-us-east-2:~/result/metric/arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_pull_pull.json @temp/result/metric/arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_pull_pull/llmc-us-east-2.json
scp llmc-ap-northeast-1:~/result/metric/arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_pull_pull.json @temp/result/metric/arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_pull_pull/llmc-ap-northeast-1.json
mkdir -p @temp/result/metric/arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_push_pull
scp llmc-us-east-2:~/result/metric/arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_push_pull.json @temp/result/metric/arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_push_pull/llmc-us-east-2.json
scp llmc-ap-northeast-1:~/result/metric/arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_push_pull.json @temp/result/metric/arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_push_pull/llmc-ap-northeast-1.json
mkdir -p @temp/result/metric/arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_push_push
scp llmc-us-east-2:~/result/metric/arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_push_push.json @temp/result/metric/arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_push_push/llmc-us-east-2.json
scp llmc-ap-northeast-1:~/result/metric/arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_push_push.json @temp/result/metric/arena_syn_mrc_100_50_tail_c2000_u150_d240_sky_push_push/llmc-ap-northeast-1.json
```

**Also, press enter to end all queue status pullers**. You should see the following output:

```bash
Queue status puller finished.
```

