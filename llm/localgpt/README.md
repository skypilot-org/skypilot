# Chat with your documents using LocalGPT and SkyPilot

<p align="center">
    <img src="https://i.imgur.com/k1IuhtV.png" alt="Logo" width="400"/>
</p>

[LocalGPT](https://github.com/PromtEngineer/localGPT) allows you to chat with your documents (txt, pdf, csv, and xlsx), ask questions and summarize content. The models run on your hardware and your data remains 100% private.
SkyPilot can run localGPT on any cloud (AWS, Azure, GCP, Lambda Cloud, IBM, Samsung, OCI) with a single command, taking care of minimizing cost and finding availability.

## Prerequisites
Install SkyPilot and check your setup of cloud credentials:
```bash
pip install git+https://github.com/skypilot-org/skypilot.git
sky check
```
See [docs](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html) for more.

Once you are done, we will use [SkyPilot YAML for localGPT](localgpt.yaml) to define our task and run it.


## Launching localGPT on your cloud with SkyPilot
1. Use `sky launch` to run localGPT on your cloud. SkyPilot will show the estimated cost and chosen cloud before provisioning. For reference, running on T4 instances on AWS would cost about $0.53 per hour. 
```bash
sky launch -c localgpt localgpt.yaml
```

2. Once you see `INFO:werkzeug:Press CTRL+C to quit`, you can safely Ctrl+C from the `sky launch` command.

3. Run `ssh -L 5111:localhost:5111 localgpt` in a new terminal window to forward the port 5111 to your local machine. Keep this terminal running.

4. Open http://localhost:5111 in your browser. Click on upload file to upload a document. Once the document has been ingested, you can chat with it, ask questions, and summarize it. For example, in the gif below, we use the SkyPilot [NSDI 2023 paper](https://www.usenix.org/system/files/nsdi23-yang-zongheng.pdf) to ask questions about how SkyPilot works.

<p align="center">
    <img src="https://i.imgur.com/0mz6DOL.gif" alt="LocalGPT demo"/>
</p>

5. Once you are done, you can terminate the instance with `sky down localgpt`.


**Optional**: To make the demo publicly accessible, configure your cloud to open port 5111 for the VPC used by your instance (see instructions for [AWS](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/authorizing-access-to-an-instance.html) and [GCP](https://cloud.google.com/vpc/docs/using-firewalls)). Then, you can access the demo at `http://<your-instance-public-ip>:5111`. You can get the IP for your instance by running:
```bash
host_name="localgpt" && grep -A1 "Host $host_name" ~/.ssh/config | awk '/HostName/ {print $2}'
```
