"""Flask inference server.

Implements a Flask server that handles inference requests for some input via a HTTP handler.
To run the server, run the following command from the root directory:
`FLASK_APP=examples/inference_server/server.py flask run`
"""

import os
import random
import re
import string
import subprocess

from flask import Flask, request, abort

from inference import INFERENCE_RESULT_MARKER
import sky

app = Flask(__name__)


def run_output(cmd: str, **kwargs) -> str:
    completed_process = subprocess.run(cmd,
                                       stdout=subprocess.PIPE,
                                       shell=True,
                                       check=True,
                                       **kwargs)
    return completed_process.stdout.decode("ascii").strip()


@app.route("/run-inference", methods=["GET"])
def run_inference():
    input = request.args.get("input")
    if not input:
        abort(400, "input query parameter is required")

    with sky.Dag() as dag:
        workdir = os.path.dirname(os.path.abspath(__file__))
        task_name = "inference_task"
        setup = 'pip3 install --upgrade pip'
        run_fn = f"python inference.py {input}"

        task = sky.Task(name=task_name,
                        setup=setup,
                        workdir=workdir,
                        run=run_fn)

        resources = sky.Resources(cloud=sky.Azure())
        task.set_resources(resources)

    cluster_name = f"inference-cluster-{''.join(random.choice(string.ascii_lowercase) for _ in range(10))}"
    sky.launch(
        dag,
        cluster_name=cluster_name,
        detach_run=True,
    )

    cmd_output = run_output(f"sky logs {cluster_name}")
    inference_result = re.findall(rf'{INFERENCE_RESULT_MARKER}((?:[^\n])+)',
                                  cmd_output)

    # Down the cluster in the background
    subprocess.Popen(f"sky down -y {cluster_name}", shell=True)

    return {"result": inference_result}
