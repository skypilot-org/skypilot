# Docker + SSH Proxy Manual Validation

This document records how I reproduced and validated SkyPilot's docker + SSH proxy flow end-to-end using a fresh jump host and the updated proxy logic.

## Environment reset

1. Reset the SkyPilot user config that the API server uses (`HOME=/tmp/sky_46582` for the systemd unit):
   ```bash
   cat <<'YAML' > /tmp/sky_46582/.sky/config.yaml
   kubernetes:
     ports: loadbalancer
     allowed_contexts:
       - kind-skypilot
   serve:
     controller:
       resources:
         infra: kubernetes
         cpus: 0.1
         memory: 0.2
   gcp:
     use_internal_ips: false
   YAML
   ```
2. Restart the SkyPilot API server so it rereads the config:
   ```bash
   systemctl --user restart skypilot-server
   ```

## Launch a new jump host

1. Bring up a public VM that will act as the SSH jump host:
   ```bash
   sky launch -y -c jump-host jump_host.yaml
   ```
   The resulting host appeared as:
   ```text
   Host jump-host
     HostName 136.115.219.220
     User gcpuser
     IdentityFile ~/.sky/generated/ssh-keys/jump-host.key
   ```

2. Update `/tmp/sky_46582/.sky/config.yaml` to force SkyPilot to route all future GCP connections through this host:
   ```bash
   cat <<'YAML' > /tmp/sky_46582/.sky/config.yaml
   kubernetes:
     ports: loadbalancer
     allowed_contexts:
       - kind-skypilot
   serve:
     controller:
       resources:
         infra: kubernetes
         cpus: 0.1
         memory: 0.2
   gcp:
     use_internal_ips: true
     ssh_proxy_command: >-
       ssh -F /home/andyl/.sky/generated/ssh/jump-host
           -o StrictHostKeyChecking=no
           -o UserKnownHostsFile=/dev/null
           -W %h:%p
           jump-host
   YAML
   ```
3. Restart the API server again:
   ```bash
   systemctl --user restart skypilot-server
   ```

## Enable outbound networking for private instances

The docker host has no public IP, so it requires a Cloud NAT for apt/docker pulls:
```bash
gcloud compute routers create sky-nat-router-benchmark \
    --network=benchmark-ultimate-e2dc6f0f-data-net-1 --region=us-central1

gcloud compute routers nats create sky-nat-benchmark \
    --router=sky-nat-router-benchmark --region=us-central1 \
    --nat-all-subnet-ip-ranges --auto-allocate-nat-external-ips
```

## Launch the docker workload

1. Use a docker image hosted on GCR (which Private Google Access can reach):
   ```yaml
   # docker_proxy_test.yaml
   resources:
     cloud: gcp
     region: us-central1
     zone: us-central1-c
     image_id: docker:gcr.io/google.com/cloudsdktool/cloud-sdk:slim
   setup: |
     echo setup inside docker
   run: |
     whoami
     echo $HOSTNAME
   ```
2. Launch the cluster:
   ```bash
   sky launch -y -c docker-proxy-test docker_proxy_test.yaml
   ```
3. Observe the output (abbreviated):
   ```text
   setup inside docker
   root
   docker-proxy-test-e2dc6f0f-head-1x1cozcw-compute
   ```
4. Verify post-launch status:
   ```bash
   sky status -v
   ```
   `docker-proxy-test` shows `UP` with only a private IP (`10.129.1.19`).

## Validate three-hop SSH and sky exec

- `sky exec docker-proxy-test -- bash -lc 'whoami && echo $HOSTNAME'` prints:
  ```text
  root
  docker-proxy-test-e2dc6f0f-head-1x1cozcw-compute
  ```
- Auto-generated SSH config (`~/.sky/generated/ssh/docker-proxy-test`) confirms the proxy chain:
  ```text
  ProxyCommand ssh -i ~/.sky/generated/ssh-keys/docker-proxy-test.key ... \
    -o ProxyCommand='ssh -F ~/.sky/generated/ssh/jump-host ... -W 10.129.1.19:22 jump-host' \
    -W %h:%p gcpuser@10.129.1.19
  ```
  This matches the intended 3-hop path: local → jump-host → host VM → container (10022).

## Cleanup (optional)

To tear everything down when finished:
```bash
sky down docker-proxy-test
ysky down jump-host

gcloud compute routers nats delete sky-nat-benchmark --router=sky-nat-router-benchmark --region=us-central1
gcloud compute routers delete sky-nat-router-benchmark --region=us-central1
```

## Result

- The docker workload completed successfully via `sky launch`. The container ran under the three-hop SSH proxy using the newly created jump host.
- Because the host VM has no public IP, it required Cloud NAT (or another egress mechanism) to download packages during provisioning. Once NAT was in place, the setup and `sky exec` both succeeded.
