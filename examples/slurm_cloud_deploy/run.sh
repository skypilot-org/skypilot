#!/bin/bash
#SBATCH --job-name=test-nodes        # name
#SBATCH --nodes=4                    # nodes
#SBATCH --ntasks-per-node=1          # crucial - only 1 task per dist per node!
#SBATCH --cpus-per-task=1           # number of cores per tasks
#SBATCH --time 0:05:00               # maximum execution time (HH:MM:SS)
#SBATCH --output=%x-%j.out           # output file name

srun --jobid $SLURM_JOBID bash -c 'hostname | tee /home/ubuntu/test.txt'

sleep 100
