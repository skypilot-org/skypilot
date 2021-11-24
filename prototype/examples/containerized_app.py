"""
Containerized app running in docker.

Runs a docker container which benchmarks the GPU by training resnet50
on a dummy imagenet dataset. As as example, this script also downloads
the MNIST dataset in setup and attaches it as volume to the  container,
illustrating how volume mounts can be used to share data with containers.
"""

import sky

# Though the mnist dataset is not used, we show download and mounting
# it to the docker container as an example here.

setup_cmd = 'mkdir ~/mnist && \
             sudo chmod 777 ~/mnist && \
             wget --no-check-certificate http://yann.lecun.com/exdb/mnist/train-images-idx3-ubyte.gz -P ~/mnist/'

run_command = 'docker run -v ~/mnist/:/mnist/ --runtime=nvidia --rm cemizm/tf-benchmark-gpu --model resnet50 --num_gpus=1'

with sky.Dag() as dag:
    t = sky.Task(run=run_command)
    t.set_resources(sky.Resources(sky.AWS(), accelerators='V100'))

dag = sky.optimize(dag)
sky.execute(dag)
