# OpenRLHF

These examples are derived from the DPO, RM Training, and SFT samples available at the following URLs — https://openrlhf.readthedocs.io/en/latest/non_rl.html & https://openrlhf.readthedocs.io/en/latest/rl.html#supervised-fine-tuning

They set up the environment within a Docker container, following the recommendations in the Quick Start guide (https://openrlhf.readthedocs.io/en/latest/quick_start.html#installation). The model sizes, batch sizes, and related parameters are scaled down to run efficiently on an 8×A100-40GB cluster and to keep execution time manageable.

All the examples use Spot Instances by default which can easily be changed by setting use_spot to False

Supervised Finetuning (SFT) example -
```
WANDB_TOKEN=xxx HF_TOKEN=xxx sky launch -c open-rlhf-sft openrlhf_sft.yaml --secret WANDB_TOKEN --secret HF_TOKEN
```

Direct Preference Optimization (DPO) example - 
```
WANDB_TOKEN=xxx sky launch -c open-rlhf-dpo openrlhf_dpo.yaml --secret WANDB_TOKEN
```

Reward Model Training example - 
```
WANDB_TOKEN=xxx sky launch -c open-rlhf-rm open-rlhf-rm-training.yaml --secret WANDB_TOKEN
```


