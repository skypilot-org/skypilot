import sky

with sky.Dag() as dag:
    train = sky.Task(
        'train',
        setup=setup,
        run=run,
    )
    train.set_resources({sky.Resources(accelerators='V100')})
