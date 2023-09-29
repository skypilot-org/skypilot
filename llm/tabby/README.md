# Tabby + SkyPilot: The AI Coding Assistant You Can Self-Host on Any Cloud

This post shows how to use SkyPilot to host an ai coding assistant with just one CLI command.

<p align="center">
    <img src="https://tabby.tabbyml.com/assets/images/staring-tabby-on-llama-cpp-8a6c61f772489b004d32b630d02ce77a.png" alt="Tabby" width="400"/>
</p>

## Background

[**Tabby**](https://github.com/TabbyML/tabby) is a self-hosted AI coding assistant, offering an open-source and on-premises alternative to GitHub Copilot. It boasts several key features:

- Self-contained, with no need for a DBMS or cloud service.
- OpenAPI interface, easy to integrate with existing infrastructure (e.g Cloud IDE).
- Supports consumer-grade GPUs.

[**SkyPilot**](https://github.com/skypilot-org/skypilot) is an open-source framework from UC Berkeley for seamlessly running machine learning on any cloud. With a simple CLI, users can easily launch many clusters and jobs, while substantially lowering their cloud bills. Currently, [AWS](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html#aws), [GCP](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html#gcp), [Azure](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html#azure), [Lambda Cloud](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html#lambda-cloud), [IBM](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html#ibm), [Oracle Cloud Infrastructure (OCI)](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html#oracle-cloud-infrastructure-oci), [Cloudflare R2](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html#cloudflare-r2) and [Samsung Cloud Platform (SCP)](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html#samsung-cloud-platform-scp) are supported. See [docs](https://skypilot.readthedocs.io/en/latest/) to learn more.

## Steps

All YAML files used below live in [the SkyPilot repo](https://github.com/skypilot-org/skypilot/tree/master/llm/tabby).

1. Install SkyPilot and [check that cloud credentials exist](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html#cloud-account-setup):

    ```bash
    # pip install skypilot
    pip install "skypilot[aws,gcp,azure,lambda]"  # pick your clouds
    sky check
    ```

2. Get the Tabby SkyPilot config:

    ```bash
    git clone https://github.com/skypilot-org/skypilot.git
    cd skypilot/llm/tabby
    ```

3. launch a Tabby cluster:
    You can select the model to be used by `--model` in the `command` field of the [docker-compose.cuda.yaml(GPU)](./docker-compose.cuda.yaml) or [docker-compose.yaml(CPU)](./docker-compose.yaml). `TabbyML/SantaCoder-1B` is used by default, for more model options, see the tabby [docs](https://tabby.tabbyml.com/docs/models/) to learn more.

    ```bash
     sky launch -c tabby tabby.yaml
    ```

<p align="center">
    <img src="https://i.imgur.com/llV1e59.png" alt="Connect the extension to your Tabby server" width="600"/>
</p>

4. After seeing `tabby server is ready, enjoy!`, you can use the following command to obtain the public IP:

    ```bash
    sky status --ip tabby
    20.92.236.53
    ```
   
    You can also use the `ssh` command directly to map the port to localhost.
    ```
    ssh -L 8080:localhost:8080 tabby
    ```

5. Install the Tabby extension in your IDE. For example, in VS Code, you can install the [Tabby extension](https://marketplace.visualstudio.com/items?itemName=TabbyML.vscode-tabby) from the marketplace.

6. Configure your IDE to use `$(sky status --ip tabby):8080` as the Tabby endpoint.

<p align="center">
    <img src="https://i.imgur.com/PTS5LNd.png" alt="Connect the extension to your Tabby server" width="400"/>
</p>

7. Open a Python file and start coding! Tabby will start suggesting code snippets as you type.

<p align="center">
    <img src="https://tabby.tabbyml.com/img/demo.gif" alt="Tabby in VS Code" width="400"/>
</p>

### Cleaning up

When you are done, you can stop or tear down the cluster:

- **To stop the cluster**, run
    ```bash
    sky stop tabby  # or pass your custom name if you used "-c <other name>"
    ```
    You can restart a stopped cluster and relaunch the tabby (the `run` section in YAML) with
    ```bash
    sky launch tabby.yaml -c tabby --no-setup
    ```
    Note the `--no-setup` flag: a stopped cluster preserves its disk contents so we can skip redoing the setup.
- **To tear down the cluster** (non-restartable), run
    ```bash
    sky down tabby  # or pass your custom name if you used "-c <other name>"
    ```
**To see your clusters**, run `sky status`, which is a single pane of glass for all your clusters across regions/clouds.

To learn more about various SkyPilot commands, see [Quickstart](https://skypilot.readthedocs.io/en/latest/getting-started/quickstart.html).
