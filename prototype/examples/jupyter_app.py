import sky


def make_application():
    """A simple application: jupyter notebook on current workspace."""

    with sky.Dag() as dag:

        setup = 'pip install --upgrade pip && \
        conda init bash && \
        conda activate jupyter || \
          (conda create -n jupyter python=3.9 -y && \
           conda activate jupyter && \
           pip install jupyter)'

        run = f'jupyter notebook --port 8888'

        jupyter_op = sky.Task('jupyter',
                              run=run,
                              setup=setup,
                              workdir='~/local/workspace',
                              ports=8888)  # int or List[int]

        jupyter_op.set_resources({
            sky.Resources(accelerators='K80', use_spot=True),
        })

    return dag


dag = make_application()
dag = sky.Optimizer.optimize(dag, minimize=sky.Optimizer.COST)
sky.execute(dag)
