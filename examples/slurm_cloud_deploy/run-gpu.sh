#!/bin/bash
#SBATCH --job-name=test-nodes        # name
#SBATCH --nodes=2                    # nodes
#SBATCH --ntasks-per-node=1          # crucial - only 1 task per dist per node!
#SBATCH --cpus-per-task=4
#SBATCH --gres=gpu:A10G:2     # match the exact GPU type from scontrol
#SBATCH --time 0:05:00               # maximum execution time (HH:MM:SS)
#SBATCH --output=%x-%j.out           # output file name
#SBATCH --partition=gpu              # use the gpu partition

# Print node and GPU information
srun --jobid $SLURM_JOBID bash -c 'echo "=== $(hostname) ===" && nvidia-smi'

# Print visible GPU devices
srun --jobid $SLURM_JOBID bash -c 'echo "CUDA_VISIBLE_DEVICES: $CUDA_VISIBLE_DEVICES"'

sleep 100
