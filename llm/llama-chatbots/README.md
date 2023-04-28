# Run LLaMA LLM chatbots on any cloud with one click
*Want to host your own LLM Chatbot on any cloud of your choosing*? Worried about GPU unavailability on the cloud, massive cloud bills, manually learning how to launch instances in different regions/clouds, or laboriously setting up the cloud instances?

This post shows how to **use** [**SkyPilot**](https://github.com/skypilot-org/skypilot) **to host a basic [LLaMA](https://ai.facebook.com/blog/large-language-model-llama-meta-ai/)-powered chatbot** with just **one CLI command**.  

It will automatically perform the following:
- **Get a beefy GPU instance** on AWS, GCP, Azure, or Lambda Labs 
- **Set up the instance** (download weights, install requirements in a Conda env, etc.)
- **Launch a chatbot interface** that we can connect to through our laptop's browser

...and it does so while abstracting away all of the above infra burden and minimizing costs.

<img src="https://i.imgur.com/G37AAMx.png" width=720 alt="Run LLaMA LLM chatbots on any cloud with SkyPilot"/>

## Background

[**LLaMA**](https://github.com/facebookresearch/llama) is a set of Large Language Models (LLMs) recently released by Meta. Trained on more than 1 trillion tokens from public datasets, LLaMA achieves high quality and is space-efficient. You can [fill out a form to request access from Meta](https://docs.google.com/forms/d/e/1FAIpQLSfqNECQnMkycAp2jP4Z9TFX0cGR4uf7b_fBxjY_OjhJILlKGA/viewform) to download the open model weights. 
In the steps below we assume either (1) you have an unexpired download URL, or (2) the weights have been downloaded and stored on the local machine.

[**SkyPilot**](https://github.com/skypilot-org/skypilot) is an open-source framework from UC Berkeley for seamlessly running machine learning on any cloud. With a simple CLI, users can easily launch many clusters and jobs, while substantially lowering their cloud bills. Currently, [Lambda Labs](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html#lambda-cloud) (low-cost GPU cloud), [AWS](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html#aws), [GCP](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html#gcp), and [Azure](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html#azure) are supported. See [docs](https://skypilot.readthedocs.io/en/latest/) to learn more.

## Steps
All YAML files used below live in [the SkyPilot repo](https://github.com/skypilot-org/skypilot/tree/master/llm/llama-chatbots), and the chatbot code is [here](https://github.com/skypilot-org/sky-llama).

0. Install SkyPilot and [check that cloud credentials exist](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html#cloud-account-setup):
    ```bash
    pip install "skypilot[aws,gcp,azure,lambda]"  # pick your clouds
    sky check
    ```
    <img src="https://i.imgur.com/7BUci5n.png" width="485" alt="`sky check` output showing enabled clouds for SkyPilot"/>

1. Get the [example folder](https://github.com/skypilot-org/skypilot/tree/master/llm/llama-chatbots):
    ```bash
    git clone https://github.com/skypilot-org/skypilot.git
    cd skypilot/llm/llama-chatbots
    ```

2.  a. **If you have an unexpired LLaMA URL from Meta**, run:

    ```bash
    export LLAMA_URL='https://'  # Add URL sent by Meta.
    sky launch llama-65b.yaml -c llama -s --env LLAMA_URL=$LLAMA_URL
    ```
    This will download the 65B model to the cloud instance. The setup process can take up to 30 minutes. 

    b. Otherwise, **if you have LLaMA checkpoints stored on your machine**: Ensure that they are organized in the following directory structure (identical to Meta's official release):
    ```
    <llama-dir>
    ├── 7B/
    |   |-- ...
    ├── 13B/
    |   |-- ...
    ├── 30B/
    |   |-- ...
    ├── 65B/
    |   |-- ...
    └── tokenizer.model
    `-- tokenizer_checklist.chk
    ```
    Then, run the commands below:
    ```bash
    export LLAMA_DIR='<llama-dir>'  # Directory of your local LLaMA checkpoints.
    ln -s $(readlink -f $LLAMA_DIR) /tmp/llama
    sky launch llama-65b-upload.yaml -c llama -s
    ```
    This will upload the 65B model to the cloud instance.

    Either way, you will see a confirmation prompt like the following:
    <img src="https://i.imgur.com/c5kZi8t.png" alt="`sky launch` output showing 8x A100 GPUs on different clouds"/>
    <p align="center"><sub>SkyPilot automatically finds the cheapest 8x A100 GPUs across clouds and regions.</sub></p>
    You may see fewer clouds because SkyPilot uses only the clouds you have access to.

    See [below](#more-commands-to-try) for many more commands to run!

3. Open another terminal and run:

    ```bash
    ssh -L 7681:localhost:7681 llama
    ```

4. Open http://localhost:7681 in your browser and start chatting!
<img src="https://i.imgur.com/vcrJx7J.png" alt="LLaMA chatbot running on the cloud via SkyPilot"/>

## More commands to try
To launch your chatbot on different GPUs or clouds, SkyPilot makes it easy with just **one argument change**. 

*NOTE*: To use checkpoints stored on your machine, replace `llama-*.yaml` with `llama-*-upload.yaml`  in all commands (see instructions above).

**Launch different GPUs** with `--gpus <type>:<num>` (see `sky show-gpus` for all supported GPUs):
```bash
sky launch llama-65b.yaml --gpus A100:8 <other args>
sky launch llama-7b.yaml --gpus A100:1 <other args>
sky launch llama-7b.yaml --gpus V100:1 <other args>
```

**Launch on different clouds** with `--cloud` (optional):
| **Cloud**                                                   | **Command**                                                                         |
| ----------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| Launch on the cheapest cloud/region (automatically chosen!) | `sky launch llama-65b.yaml -c llama-65b -s --env LLAMA_URL=$LLAMA_URL`                |
| Launch on Lambda Labs                                       | `sky launch llama-65b.yaml --cloud lambda -c llama-65b -s --env LLAMA_URL=$LLAMA_URL` |
| Launch on GCP                                               | `sky launch llama-65b.yaml --cloud gcp -c llama-65b -s --env LLAMA_URL=$LLAMA_URL`    |
| Launch on AWS                                               | `sky launch llama-65b.yaml --cloud aws -c llama-65b -s --env LLAMA_URL=$LLAMA_URL`    |
| Launch on Azure                                             | `sky launch llama-65b.yaml --cloud azure -c llama-65b -s --env LLAMA_URL=$LLAMA_URL`  |


**Use spot instances** to save >3x costs with `--use-spot`:
```bash
sky launch llama-65b.yaml --use-spot <other args>
```

**To use other model sizes**, simply pass the correct YAML path to the command (the YAMLs have set the correct number of GPUs and checkpoint paths):
* [`llama-7b.yaml`](./llama-7b.yaml)
* [`llama-13b.yaml`](./llama-13b.yaml)
* [`llama-30b.yaml`](./llama-30b.yaml)
* [`llama-65b.yaml`](./llama-65b.yaml)
```bash
sky launch llama-7b.yaml -c llama-7b -s --env LLAMA_URL=$LLAMA_URL
sky launch llama-13b.yaml -c llama-13b -s --env LLAMA_URL=$LLAMA_URL
sky launch llama-30b.yaml -c llama-30b -s --env LLAMA_URL=$LLAMA_URL
sky launch llama-65b.yaml -c llama-65b -s --env LLAMA_URL=$LLAMA_URL
```

To see details about these flags, see [CLI docs](https://skypilot.readthedocs.io/en/latest/reference/cli.html) or run `sky launch -h`.

## Cleaning up
When you are done, you can stop or tear down the cluster:

- **To stop the cluster**, run
    ```bash
    sky stop llama  # or pass your custom name if you used "-c <other name>"
    ```
    You can restart a stopped cluster and relaunch the chatbot (the `run` section in YAML) with
    ```bash
    sky launch llama-65b.yaml -c llama --no-setup
    ```
    Note the `--no-setup` flag: a stopped cluster preserves its disk contents so we can skip redoing the setup.
- **To tear down the cluster** (non-restartable), run
    ```bash
    sky down llama  # or pass your custom name if you used "-c <other name>"
    ```
**To see your clusters**, run `sky status`, which is a single pane of glass for all your clusters across regions/clouds.

To learn more about various SkyPilot commands, see [Quickstart](https://skypilot.readthedocs.io/en/latest/getting-started/quickstart.html).


## Why SkyPilot?

Some caveat first. LLaMA models aren't specifically finetuned for being used as a chatbot and we only did some basic priming of the model (`INIT_PROMPT` in [`chat.py`](https://github.com/skypilot-org/sky-llama/blob/main/chat.py#L39-L53)), so it is expected to experience not-so-great quality in your chats. See also Meta's [FAQ here](https://github.com/facebookresearch/llama/blob/main/FAQ.md#2-generations-are-bad).

That said, we expect LLaMAs/other open LLMs to rapidly advance in the near future. As open LLMs become more powerful, bigger, and more compute-hungry, the demand of **flexibly finetuning and running them on a variety of cloud compute** will dramatically increase. 

And that is where SkyPilot comes in.  This example shows three major benefits of using SkyPilot to run ML projects on the cloud:

**Cloud portability & productivity**: We've wrapped an existing ML project and launched it to the cloud of your choosing using a simple YAML and one command. Interacting with a simple CLI, users get *cloud portability* with one argument change. 

SkyPilot also improves ML users' productivity of using the cloud. There's no need to learn different clouds' consoles or APIs. No need to figure out the right instance types. And there's **no change to the actual project code** for it to run. 

**Higher GPU availability**: If a region or a whole cloud runs out of GPUs (increasingly common in today's Large Models race), the only solution other than waiting is to **go to more regions and clouds**. 

![SkyPilot's auto-failover across regions and clouds to improve GPU availability](https://i.imgur.com/zDl2zob.png)
<p align="center"><sub>SkyPilot's auto-failover. Regions from all enabled clouds are sorted by price and attempted in that order. If a launch request is constrained to use a specific cloud, only that cloud's regions are used in failover. Ordering in figure is illustrative and may not reflect the most up-to-date prices.</sub></p>

SkyPilot's `sky launch` command makes this entirely automatic. It performs *auto-failover* behind the scenes. For each request the system loops through all enabled regions (or even clouds) to find available GPUs, and does so in the cheapest-price order.

**Reducing cloud bills**: GPUs can be very expensive on the cloud. SkyPilot reduces ML teams' costs by supporting 
- low-cost GPU cloud (Lambda; >3x cheaper than AWS/Azure/GCP) 
- spot instances (>3x cheaper than on-demand)
- automatically choosing the cheapest cloud/region/zone
- auto-stopping & auto-termination of instances ([docs](https://skypilot.readthedocs.io/en/latest/reference/auto-stop.html))

## Recap

Congratulations! You have used SkyPilot to launch a LLaMA-based chatbot on the cloud with just one command. The system automatically handles setting up instances and it offers cloud portability, higher GPU availability, and cost reduction.

LLaMA chatbots are just one example app. To leverage these benefits for your own ML projects on the cloud, we recommend the [Quickstart guide](https://skypilot.readthedocs.io/en/latest/getting-started/quickstart.html).

*Feedback or questions? Want to run other LLM models?* Feel free to drop a note to the SkyPilot team on [GitHub](https://github.com/skypilot-org/skypilot/) or [Slack](http://slack.skypilot.co/) and we're happy to chat!
