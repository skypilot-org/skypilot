# Run FlexGen on the cloud in 4 easy steps!

[FlexGen](https://github.com/FMInference/FlexGen) is a high-throughput generation engine for running large language models with limited GPU memory (e.g., a 16GB T4 GPU or a 24GB RTX3090 gaming card!)

What if you do not own a GPU with sufficient memory? You can **use SkyPilot to run FlexGen on the cloud with just a single command!**

What's more - we'll create a webui so you can interact with FlexGen straight from your browser!

![](https://i.imgur.com/DpgZXkX.png)

And don't worry about costs - SkyPilot will automatically find the lowest priced VM instance type across different clouds, and provision the VM with auto-failover if the cloud returned capacity errors. Moreover, you can also run this on spot instances to reduce your costs by upto 6x!

## How to launch FlexGen on the cloud with SkyPilot
1. Install SkyPilot and check your cloud credential configuration. See [installation docs](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html) for more.
    ```
    pip install "skypilot[all]"
    sky check
    ```

2. Launch FlexGen on your desired cloud with SkyPilot. This will automatically provision a VM instance on the cloud, install FlexGen, and launch the webui. You can also not specify the `--cloud` option to let SkyPilot pick the cheapest cloud for you!
    ```
    sky launch flexgen.yaml -c flexgen -d --cloud gcp --gpus T4:1
    ```

3. Open a new terminal to setup SSH port forwarding. Keep this terminal open. This will allow you to access the webui from your browser.
    ```
    ssh -L 7681:localhost:7681 flexgen
    ```
   
4. Open the webui in your browser at http://localhost:7681 and start chatting!

When you're done, run terminate the VM instance with:
```
sky down flexgen
```

### Notes
* First time you open the browser, it may take some time to download the model. Subsequent loads should be much faster.
* To restart the conversation simply refresh the page. Depending on your chosen GPU, you would likely be able to have only one tab running at a time!
* You can try other models too! See the comments at the end of flexgen.yaml for more details.




