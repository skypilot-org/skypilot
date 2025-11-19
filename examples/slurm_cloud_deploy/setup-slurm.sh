# This is a script to be run on the controller node to setup the slurm cluster,
# after using SkyPilot's way to deploy slurm on multiple clusters.
# 1. `sky launch -c slurm --num-nodes 2 --gpus A10G:4 deploy.yaml`
# 2. `sky launch -c slurm-l4 --num-nodes 2 --gpus L4:4 deploy.yaml`
# 3. `sky launch -c slurm-l4-uswest --num-nodes 2 --gpus L4:4 deploy.yaml`
# 4. run `add-users.yaml` on all the clusters as well
# 5. copy the ssh config to the `slurm` head node with the ssh key
# 6. setup the /etc/slurm/slurm.conf and /etc/slurm/gres.conf on the `slurm`
#    head node
# 7. run `setup-slurm.sh` on the `slurm` head node

set -e

IPS="slurm-l4
slurm-l4-worker1
slurm-l4-uswest
slurm-l4-uswest-worker1
slurm-worker1
"


for ip in $IPS; do
    echo "Processing $ip"
    scp -o StrictHostKeyChecking=no /tmp/munge.key $ip:/tmp/munge.key
    ssh -o StrictHostKeyChecking=no $ip "sudo mv /tmp/munge.key /etc/munge/munge.key && sudo chown munge:munge /etc/munge/munge.key && sudo chmod 400 /etc/munge/munge.key && sudo systemctl restart munge"
    rsync -avz -e "ssh -o StrictHostKeyChecking=no" --rsync-path="sudo rsync" /etc/slurm/slurm.conf $ip:/etc/slurm/slurm.conf
    rsync -avz -e "ssh -o StrictHostKeyChecking=no" --rsync-path="sudo rsync" /etc/slurm/gres.conf $ip:/etc/slurm/gres.conf
    ssh -o StrictHostKeyChecking=no $ip "sudo systemctl restart slurmd"
done

sudo systemctl restart slurmctld
