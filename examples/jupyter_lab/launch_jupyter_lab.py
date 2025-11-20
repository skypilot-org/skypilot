"""Example: Launch Jupyter Lab with a password configured
and auto-expose its port to Internet."""
import os

from jupyter_server import auth as jupyter_auth

import sky

_JUPYTER_PORT = 29324
_JUPYTER_PASSWORD_ENV_VAR = 'JUPYTER_PASSWORD'


def launch_jupyterlab_cluster(cluster_name: str):
    """Launches a cluster with JupyterLab installed and running."""
    password = os.environ[_JUPYTER_PASSWORD_ENV_VAR]
    hashed_password = jupyter_auth.passwd(password)
    secrets = {
        _JUPYTER_PASSWORD_ENV_VAR: hashed_password,
    }

    resources = sky.Resources(
        infra='aws',
        cpus='4+',
        memory='16+',
        ports=_JUPYTER_PORT,
        # uncomment/modify to use GPUs
        # accelerators='A100:8',
    )

    run = [
        'jupyter server --generate-config',
        ('echo "c.PasswordIdentityProvider.hashed_password = '
         f'u\'${_JUPYTER_PASSWORD_ENV_VAR}\'" '
         '>> ~/.jupyter/jupyter_server_config.py'),
        f'jupyter lab --port={_JUPYTER_PORT} --no-browser --ip=0.0.0.0',
    ]
    task = sky.Task(
        setup='pip install jupyterlab',
        run=run,
        resources=resources,
        secrets=secrets,
    )

    # sky.stream_and_get can be replaced with sky.get
    # if you don't want to stream the logs.
    sky.stream_and_get(sky.launch(
        task,
        cluster_name=cluster_name,
    ))

    ports = sky.get(sky.endpoints(cluster=cluster_name, port=_JUPYTER_PORT))
    host_and_port = list(ports.values())[0]
    print(f'JupyterLab will be available at http://{host_and_port}')

    # stop the cluster.
    # sky.stream_and_get can be replaced with sky.get
    # if you don't want to stream the logs
    # sky.stream_and_get(sky.stop(cluster_name))

    # terminate the cluster.
    # sky.stream_and_get can be replaced with sky.get
    # if you don't want to stream the logs
    # sky.stream_and_get(sky.down(cluster_name))


if __name__ == '__main__':
    launch_jupyterlab_cluster('jupyter-lab-example')
