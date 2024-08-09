import apex


def make_application():
    """A simple application: jupyter notebook on current workspace."""

    with apex.Dag() as dag:

        setup = 'pip install --upgrade pip && \
        conda init bash && \
        conda activate jupyter || \
          (conda create -n jupyter python=3.9 -y && \
           conda activate jupyter && \
           pip install jupyter)'

        run = f'jupyter notebook --port 8888'

        # Use 'ssh -L 8888:localhost:8888 <cluster_name>' to forward port to local.
        # 'ssh -L 8888:localhost:8888 sky-12345'
        jupyter = apex.Task('jupyter',
                           run=run,
                           setup=setup,
                           workdir='~/local/workspace')

        jupyter.set_resources({
            apex.Resources(accelerators='K80', use_spot=True),
        })

    return dag


dag = make_application()
apex.launch(dag)
