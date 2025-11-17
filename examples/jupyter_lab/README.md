# Run JupyterLab on SkyPilot

Run a personal Jupyter Lab server on a SkyPilot cluster.

## Launch with CLI

Launch a Jupyter Lab cluster with the command:

` ``bash
sky launch -c jupyter-lab-example jupyter_lab.yaml`
` ``

Look for the following lines in the output for the link to the web UI.
```
Jupyter Server x.x.x is running at:
     http://127.0.0.1:29324/lab?token=<token>
```

Run

`sky status jupyter-lab-example --endpoints`

to get the `HEAD_IP` of the cluster, replace the `127.0.0.1` with the `HEAD_IP` and open browser for the URL.

## Launch with SDK

Launch a Jupyter Lab cluster with the command:

`JUPYTER_PASSWORD=jupyter-password python launch_jupyter_lab.py`

Look for the following output for the link to the web UI.
```
JupyterLab will be available at http://xx.xx.xx.xx:29324
```

Use the password set in the command to log in to the web UI.
