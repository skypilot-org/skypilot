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

Running the following command for 4 times. Notice the last one has `DO_PUSHING_ACROSS_LB=true` to enable 2 level pushing.

```bash
sky serve up examples/serve/external-lb/llm.yaml --env HF_TOKEN -n f1
sky serve up examples/serve/external-lb/llm.yaml --env HF_TOKEN -n f2
sky serve up examples/serve/external-lb/llm.yaml --env HF_TOKEN -n f3
sky serve up examples/serve/external-lb/llm.yaml --env HF_TOKEN -n f4 --env DO_PUSHING_ACROSS_LB=true
```

Keep running `sky serve status -v` until all of them are ready (all replicas are ready):

```bash
$ sky serve status -v
Services
NAME  VERSION  UPTIME   STATUS  REPLICAS  EXTERNAL_LBS  ENDPOINT                 AUTOSCALING_POLICY  LOAD_BALANCING_POLICY  REQUESTED_RESOURCES  
f2    1        11m 37s  READY   4/4       2/2           f2.aws.cblmemo.net:8000  Fixed 4 replicas    prefix_tree            1x[L4:1]             
f4    1        2m 40s   READY   4/4       2/2           f4.aws.cblmemo.net:8000  Fixed 4 replicas    prefix_tree            1x[L4:1]             
f3    1        3m 52s   READY   4/4       2/2           f3.aws.cblmemo.net:8000  Fixed 4 replicas    prefix_tree            1x[L4:1]             
f1    1        12m 24s  READY   4/4       2/2           f1.aws.cblmemo.net:8000  Fixed 4 replicas    prefix_tree            1x[L4:1]             

Service Replicas
SERVICE_NAME  ID  VERSION  ENDPOINT                    LAUNCHED     RESOURCES                                     STATUS  REGION          ZONE             
f2            1   1        http://3.112.132.61:8081    17 mins ago  1x AWS(g6.xlarge, {'L4': 1}, ports=['8081'])  READY   ap-northeast-1  ap-northeast-1a  
f2            2   1        http://18.216.72.120:8081   17 mins ago  1x AWS(g6.xlarge, {'L4': 1}, ports=['8081'])  READY   us-east-2       us-east-2a       
f2            3   1        http://13.230.8.93:8081     17 mins ago  1x AWS(g6.xlarge, {'L4': 1}, ports=['8081'])  READY   ap-northeast-1  ap-northeast-1a  
f2            4   1        http://18.188.161.112:8081  17 mins ago  1x AWS(g6.xlarge, {'L4': 1}, ports=['8081'])  READY   us-east-2       us-east-2a       
f4            1   1        http://52.197.215.252:8081  8 mins ago   1x AWS(g6.xlarge, {'L4': 1}, ports=['8081'])  READY   ap-northeast-1  ap-northeast-1a  
f4            2   1        http://18.216.115.216:8081  8 mins ago   1x AWS(g6.xlarge, {'L4': 1}, ports=['8081'])  READY   us-east-2       us-east-2a       
f4            3   1        http://13.230.232.136:8081  8 mins ago   1x AWS(g6.xlarge, {'L4': 1}, ports=['8081'])  READY   ap-northeast-1  ap-northeast-1a  
f4            4   1        http://3.147.237.86:8081    8 mins ago   1x AWS(g6.xlarge, {'L4': 1}, ports=['8081'])  READY   us-east-2       us-east-2a       
f3            1   1        http://3.112.189.75:8081    9 mins ago   1x AWS(g6.xlarge, {'L4': 1}, ports=['8081'])  READY   ap-northeast-1  ap-northeast-1a  
f3            2   1        http://18.118.12.144:8081   9 mins ago   1x AWS(g6.xlarge, {'L4': 1}, ports=['8081'])  READY   us-east-2       us-east-2a       
f3            3   1        http://18.179.31.22:8081    9 mins ago   1x AWS(g6.xlarge, {'L4': 1}, ports=['8081'])  READY   ap-northeast-1  ap-northeast-1a  
f3            4   1        http://18.117.137.72:8081   9 mins ago   1x AWS(g6.xlarge, {'L4': 1}, ports=['8081'])  READY   us-east-2       us-east-2a       
f1            1   1        http://52.195.12.96:8081    17 mins ago  1x AWS(g6.xlarge, {'L4': 1}, ports=['8081'])  READY   ap-northeast-1  ap-northeast-1a  
f1            2   1        http://3.133.128.90:8081    18 mins ago  1x AWS(g6.xlarge, {'L4': 1}, ports=['8081'])  READY   us-east-2       us-east-2a       
f1            3   1        http://35.77.104.21:8081    17 mins ago  1x AWS(g6.xlarge, {'L4': 1}, ports=['8081'])  READY   ap-northeast-1  ap-northeast-1a  
f1            4   1        http://18.188.39.165:8081   18 mins ago  1x AWS(g6.xlarge, {'L4': 1}, ports=['8081'])  READY   us-east-2       us-east-2a       

External Load Balancers
SERVICE_NAME  ID  VERSION  ENDPOINT                    LAUNCHED     RESOURCES                          STATUS  REGION          ZONE             
f2            1   1        http://3.21.105.214:8000    17 mins ago  1x AWS(m6i.large, ports=['8000'])  READY   us-east-2       us-east-2a       
f2            2   1        http://54.199.244.142:8000  17 mins ago  1x AWS(m6i.large, ports=['8000'])  READY   ap-northeast-1  ap-northeast-1a  
f4            1   1        http://3.22.61.20:8000      8 mins ago   1x AWS(m6i.large, ports=['8000'])  READY   us-east-2       us-east-2a       
f4            2   1        http://54.95.39.148:8000    8 mins ago   1x AWS(m6i.large, ports=['8000'])  READY   ap-northeast-1  ap-northeast-1a  
f3            1   1        http://3.147.126.69:8000    9 mins ago   1x AWS(m6i.large, ports=['8000'])  READY   us-east-2       us-east-2a       
f3            2   1        http://13.115.40.251:8000   9 mins ago   1x AWS(m6i.large, ports=['8000'])  READY   ap-northeast-1  ap-northeast-1a  
f1            1   1        http://18.189.184.32:8000   18 mins ago  1x AWS(m6i.large, ports=['8000'])  READY   us-east-2       us-east-2a       
f1            2   1        http://52.194.243.253:8000  18 mins ago  1x AWS(m6i.large, ports=['8000'])  READY   ap-northeast-1  ap-northeast-1a 
```

## Step 2: Launch baseline load balancers

We compare the performance of our load balancer with the following baselines:

- SGLang Router
- SGLang Router with Rate Limiting

The following util script will launch the baseline load balancers for the given service names. **The order of the service names matters here**. The first will be used as SGLang Router and the second will be used as SGLang Router with Rate Limiting. The third is our solution.

```bash
python3 -m sky.lbbench.launch_lb --service-names f1 f2 f3 f4
```

Press enter to confirm and launch the load balancers. After the script exits, run the following command to check the status of the load balancers. You should see the following output:

```bash
$ sky logs router
...
(task, pid=2032) [Router (Rust)] 2025-04-22 21:56:49 - INFO - All workers are healthy
(task, pid=2032) [Router (Rust)] 2025-04-22 21:56:49 - INFO - ✅ Serving router on 0.0.0.0:9001
(task, pid=2032) [Router (Rust)] 2025-04-22 21:56:49 - INFO - ✅ Serving workers on ["http://52.195.12.96:8081", "http://3.133.128.90:8081", "http://35.77.104.21:8081", "http://18.188.39.165:8081"]
```

```bash
$ sky logs sky-global
(load-balancer, pid=2023) INFO:__main__:All ready LB URLs: {'us-east-2': ['http://3.21.105.214:8000'], 'ap-northeast-1': ['http://54.199.244.142:8000']}
(load-balancer, pid=2023) INFO:__main__:Available Replica URLs: {'ap-northeast-1': ['http://3.112.132.61:8081', 'http://13.230.8.93:8081'], 'us-east-2': ['http://18.216.72.120:8081', 'http://18.188.161.112:8081']}, Ready URLs in local region global: ['http://3.112.132.61:8081', 'http://13.230.8.93:8081', 'http://18.216.72.120:8081', 'http://18.188.161.112:8081']
```

Make sure each load balancer has the desired number of replicas.

## Step 3: Generate Commands

We have a util script to generate the benchmark commands. This doc will only cover the usage of multi-region clients, which means the requests will be simultaneously sent from multiple regions.

**Notice that the service names should be the same order as the ones used in Step 2**.

Explanation of the arguments:

- `--exp-name`: Identifier for the experiment. Please describe the experiment config in the name.
- `--extra-args`: Workload specific arguments.
- `--regions`: Client regions. This should be a list.

**Notice that the `--extra-args` will be applied to all regions**. If you want a total concurrency of 300, you should set `--num-users (300 / num-regions)` for each region.

```bash
python3 -m sky.lbbench.gen_cmd --service-names f1 f2 f3 f4 \
  --exp-name arena_syn_mrc_tail_c2000_u300_d240 \
  --extra-args '--workload arena_syn --duration 240 --num-conv 2000 --num-users 150' \
  --regions us-east-2 ap-northeast-1
```

### Side Note: Support for different configurations in different regions

For testing config that different regions have different configurations, you can use `--region-to-args`. This should be a json string. e.g.

```bash
python3 -m sky.lbbench.gen_cmd --service-names f1 f2 f3 f4 \
  --exp-name arena_syn_mrc_50_200_tail_c2000_u250_d240 \
  --extra-args '--workload arena_syn --duration 240' \
  --region-to-args '{"us-east-2":"--num-users 200","ap-northeast-1":"--num-users 50"}'
```

Only one of the `--regions` and `--region-to-args` should be set. If `--region-to-args` is set, the keys will be used. Remember to remove any arguments from `--extra-args` that are already specified in `--region-to-args`.

You should see a group of commands printed out. Follow next steps to run them.

## Step 4: Run the commands

You will need a lot of terminals to run the commands. Specifically, #regions + #systems (2 + 3 in the default configurations). There are 3 types of commands:

- Queue status puller (running locally): pull the queue status from the load balancers and save them to the local directory.
- Launch clients: launch the clients in the given regions.
- Sync down results: sync down the results from the load balancers to the local directory.

### Queue status puller

You will see #systems lines of commands. Each line will pull status from one system. They are separated by 30 `*`s.

```bash
================Queue status puller (Running locally)=================
python3 -m sky.lbbench.bench --exp-name arena_syn_mrc_50_200_tail_c2000_u250_d240_sgl --backend-url 184.73.149.39:9001 --workload arena_syn --duration 240 --skip-tasks
******************************
python3 -m sky.lbbench.bench --exp-name arena_syn_mrc_50_200_tail_c2000_u250_d240_sky_sgl_enhanced --backend-url 44.202.34.180:9002 --workload arena_syn --duration 240 --skip-tasks
******************************
python3 -m sky.lbbench.bench --exp-name arena_syn_mrc_50_200_tail_c2000_u250_d240_sky --backend-url f3.aws.cblmemo.net:8000 --workload arena_syn --duration 240 --skip-tasks
******************************
python3 -m sky.lbbench.bench --exp-name arena_syn_mrc_50_200_tail_c2000_u250_d240_sky_pushing --backend-url f4.aws.cblmemo.net:8000 --workload arena_syn --duration 240 --skip-tasks
******************************
```

Running each of them in a separate terminal. **Press enter to confirm after each command**. You should see the following output after confirmation:

```bash
Pulling queue status:      tail -f <log-file-name>
```

Keep them running until the experiment finishes.

### Launch clients

You will see #regions lines of commands. Each line will launch clients in one region. They are separated by 30 `*`s as well.

```bash
============================Launch Clients============================
sky launch --region us-east-2 -c llmc-us-east-2 --detach-run -y --env CMD='python3 -m sky.lbbench.bench --exp-name arena_syn_mrc_50_200_tail_c2000_u250_d240_sgl --backend-url 184.73.149.39:9001 --workload arena_syn --duration 240 --skip-queue-status --output-dir ~ -y --seed us-east-2 --num-users 200' --env HF_TOKEN examples/serve/external-lb/client.yaml
sky launch --region us-east-2 -c llmc-us-east-2 --detach-run -y --env CMD='python3 -m sky.lbbench.bench --exp-name arena_syn_mrc_50_200_tail_c2000_u250_d240_sky_sgl_enhanced --backend-url 44.202.34.180:9002 --workload arena_syn --duration 240 --skip-queue-status --output-dir ~ -y --seed us-east-2 --num-users 200' --env HF_TOKEN examples/serve/external-lb/client.yaml
sky launch --region us-east-2 -c llmc-us-east-2 --detach-run -y --env CMD='python3 -m sky.lbbench.bench --exp-name arena_syn_mrc_50_200_tail_c2000_u250_d240_sky --backend-url f3.aws.cblmemo.net:8000 --workload arena_syn --duration 240 --skip-queue-status --output-dir ~ -y --seed us-east-2 --num-users 200' --env HF_TOKEN examples/serve/external-lb/client.yaml
sky launch --region us-east-2 -c llmc-us-east-2 --detach-run -y --env CMD='python3 -m sky.lbbench.bench --exp-name arena_syn_mrc_50_200_tail_c2000_u250_d240_sky_pushing --backend-url f4.aws.cblmemo.net:8000 --workload arena_syn --duration 240 --skip-queue-status --output-dir ~ -y --seed us-east-2 --num-users 200' --env HF_TOKEN examples/serve/external-lb/client.yaml
******************************
sky launch --region ap-northeast-1 -c llmc-ap-northeast-1 --detach-run -y --env CMD='python3 -m sky.lbbench.bench --exp-name arena_syn_mrc_50_200_tail_c2000_u250_d240_sgl --backend-url 184.73.149.39:9001 --workload arena_syn --duration 240 --skip-queue-status --output-dir ~ -y --seed ap-northeast-1 --num-users 50' --env HF_TOKEN examples/serve/external-lb/client.yaml
sky launch --region ap-northeast-1 -c llmc-ap-northeast-1 --detach-run -y --env CMD='python3 -m sky.lbbench.bench --exp-name arena_syn_mrc_50_200_tail_c2000_u250_d240_sky_sgl_enhanced --backend-url 44.202.34.180:9002 --workload arena_syn --duration 240 --skip-queue-status --output-dir ~ -y --seed ap-northeast-1 --num-users 50' --env HF_TOKEN examples/serve/external-lb/client.yaml
sky launch --region ap-northeast-1 -c llmc-ap-northeast-1 --detach-run -y --env CMD='python3 -m sky.lbbench.bench --exp-name arena_syn_mrc_50_200_tail_c2000_u250_d240_sky --backend-url f3.aws.cblmemo.net:8000 --workload arena_syn --duration 240 --skip-queue-status --output-dir ~ -y --seed ap-northeast-1 --num-users 50' --env HF_TOKEN examples/serve/external-lb/client.yaml
sky launch --region ap-northeast-1 -c llmc-ap-northeast-1 --detach-run -y --env CMD='python3 -m sky.lbbench.bench --exp-name arena_syn_mrc_50_200_tail_c2000_u250_d240_sky_pushing --backend-url f4.aws.cblmemo.net:8000 --workload arena_syn --duration 240 --skip-queue-status --output-dir ~ -y --seed ap-northeast-1 --num-users 50' --env HF_TOKEN examples/serve/external-lb/client.yaml
******************************
```

Running each of them in a separate terminal. They will **sequentially** launch the clients for all experiment in the same order and exit after the experiment is scheduled.

**You can just copy-pasting each group of them and press enter all at once**. They will be executed sequentially. You should see **#systems output like this for each command**:

```bash
📋 Useful Commands
Job ID: 15
├── To cancel the job:          sky cancel llmc-us-east-2 15
├── To stream job logs:         sky logs llmc-us-east-2 15
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
3   -     tianxia  39 secs ago  34 secs ago  34s       1x[CPU:8+]  RUNNING  ~/sky_logs/sky-2025-04-22-15-03-16-697533  
2   -     tianxia  1 min ago    1 min ago    1m 10s    1x[CPU:8+]  RUNNING  ~/sky_logs/sky-2025-04-22-15-02-40-245912  
1   -     tianxia  1 min ago    1 min ago    1m 42s    1x[CPU:8+]  RUNNING  ~/sky_logs/sky-2025-04-22-15-01-29-838721  
4   -     tianxia  46 secs ago  42 secs ago  42s       1x[CPU:8+]  RUNNING  ~/sky_logs/sky-2025-04-22-15-03-18-322517  
3   -     tianxia  1 min ago    1 min ago    1m 8s     1x[CPU:8+]  RUNNING  ~/sky_logs/sky-2025-04-22-15-02-52-637991  
2   -     tianxia  1 min ago    1 min ago    1m 34s    1x[CPU:8+]  RUNNING  ~/sky_logs/sky-2025-04-22-15-02-26-056918  
1   -     tianxia  2 mins ago   1 min ago    1m 56s    1x[CPU:8+]  RUNNING  ~/sky_logs/sky-2025-04-22-15-01-28-622966 
...
```

Keep running the same command and wait until it reaches `SUCCEEDED`. This should end in the duration you specified in the `--extra-args`.

```bash
$ sky queue llmc-ap-northeast-1 llmc-us-east-2
Fetching and parsing job queue...
Fetching job queue for: llmc-ap-northeast-1, llmc-us-east-2

Job queue of current user on cluster llmc-us-east-2
ID  NAME  USER     SUBMITTED   STARTED     DURATION  RESOURCES   STATUS     LOG                                        
15  -     tianxia  6 mins ago  6 mins ago  4m 45s    1x[CPU:8+]  SUCCEEDED  ~/sky_logs/sky-2025-04-22-08-58-07-411053  
14  -     tianxia  6 mins ago  6 mins ago  4m 39s    1x[CPU:8+]  SUCCEEDED  ~/sky_logs/sky-2025-04-22-08-57-43-016507  
13  -     tianxia  7 mins ago  7 mins ago  4m 49s    1x[CPU:8+]  SUCCEEDED  ~/sky_logs/sky-2025-04-22-08-56-52-814746  

Job queue of current user on cluster llmc-ap-northeast-1
ID  NAME  USER     SUBMITTED   STARTED     DURATION  RESOURCES   STATUS     LOG                                        
15  -     tianxia  5 mins ago  5 mins ago  4m 27s    1x[CPU:8+]  SUCCEEDED  ~/sky_logs/sky-2025-04-22-08-58-34-066940  
14  -     tianxia  6 mins ago  6 mins ago  4m 24s    1x[CPU:8+]  SUCCEEDED  ~/sky_logs/sky-2025-04-22-08-57-58-882792  
13  -     tianxia  7 mins ago  7 mins ago  4m 36s    1x[CPU:8+]  SUCCEEDED  ~/sky_logs/sky-2025-04-22-08-56-53-480548  
```

**If it does not ends with status `SUCCEEDED`, you can check the logs for more details**.

```bash
$ sky logs llmc-us-east-2 15
```

### Sync down results

You will see a group of commands. Each line will sync down the results from one client on one system.

**You can just copy-pasting all of them and press enter all at once**. Check for any unusual error output like file not found.

```bash
==========================Sync down results===========================
mkdir -p @temp/result/metric/arena_syn_mrc_100_200_tail_c2000_u300_d240_sgl
scp llmc-us-east-2:~/result/metric/arena_syn_mrc_100_200_tail_c2000_u300_d240_sgl.json @temp/result/metric/arena_syn_mrc_100_200_tail_c2000_u300_d240_sgl/llmc-us-east-2.json
scp llmc-ap-northeast-1:~/result/metric/arena_syn_mrc_100_200_tail_c2000_u300_d240_sgl.json @temp/result/metric/arena_syn_mrc_100_200_tail_c2000_u300_d240_sgl/llmc-ap-northeast-1.json
mkdir -p @temp/result/metric/arena_syn_mrc_100_200_tail_c2000_u300_d240_sky_sgl_enhanced
scp llmc-us-east-2:~/result/metric/arena_syn_mrc_100_200_tail_c2000_u300_d240_sky_sgl_enhanced.json @temp/result/metric/arena_syn_mrc_100_200_tail_c2000_u300_d240_sky_sgl_enhanced/llmc-us-east-2.json
scp llmc-ap-northeast-1:~/result/metric/arena_syn_mrc_100_200_tail_c2000_u300_d240_sky_sgl_enhanced.json @temp/result/metric/arena_syn_mrc_100_200_tail_c2000_u300_d240_sky_sgl_enhanced/llmc-ap-northeast-1.json
mkdir -p @temp/result/metric/arena_syn_mrc_100_200_tail_c2000_u300_d240_sky
scp llmc-us-east-2:~/result/metric/arena_syn_mrc_100_200_tail_c2000_u300_d240_sky.json @temp/result/metric/arena_syn_mrc_100_200_tail_c2000_u300_d240_sky/llmc-us-east-2.json
scp llmc-ap-northeast-1:~/result/metric/arena_syn_mrc_100_200_tail_c2000_u300_d240_sky.json @temp/result/metric/arena_syn_mrc_100_200_tail_c2000_u300_d240_sky/llmc-ap-northeast-1.json
```

**Also, press enter to end all queue status pullers**. You should see the following output:

```bash
Queue status puller finished.
```

### Plot

Final step is to plot the results. You should see the following output from the gen cmd script:

```bash
========================Generate result table=========================
    'arena_syn_mrc_100_200_tail_c2000_u300_d240_sgl': 'Baseline',
    'arena_syn_mrc_100_200_tail_c2000_u300_d240_sky_sgl_enhanced': 'Baseline\n[Enhanced]',
    'arena_syn_mrc_100_200_tail_c2000_u300_d240_sky': 'Ours',
```

Copy-pasting them into the `gn2alias` variable in the `@temp/result/plot.py` script and run it. **Make sure to comment out other parts in the variable**.

```bash
python3 @temp/result/plot.py
```

You should see the figures in the `@temp/result/fig` directory.

## Step 5: Cleanup

**Notice: please terminate all the clusters after use**.

```bash
sky serve down -ay
sky stop -ay
```

