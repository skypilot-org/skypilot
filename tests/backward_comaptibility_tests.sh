#!/bin/bash
set -ex

CLUSTER_NAME="test-back-compat-$USER"
. $(conda info --base 2> /dev/null)/etc/profile.d/conda.sh

git clone https://github.com/skypilot-org/skypilot.git sky-master || true


# Create environment for compatibility tests
conda create -n sky-back-compat-master -y --clone sky-dev
conda create -n sky-back-compat-current -y --clone sky-dev

conda activate sky-back-compat-master
rm -r  ~/.sky/wheels || true
pip uninstall skypilot -y

cd sky-master
git pull origin master
pip install -e ".[all]"
cd -

conda activate sky-back-compat-current
rm -r  ~/.sky/wheels || true
pip uninstall -y skypilot
pip install -e ".[all]"


# exec + launch
conda activate sky-back-compat-master
rm -r  ~/.sky/wheels || true
which sky
sky launch --cloud gcp -c ${CLUSTER_NAME} examples/minimal.yaml

conda activate sky-back-compat-current
rm -r  ~/.sky/wheels || true
sky exec --cloud gcp ${CLUSTER_NAME} examples/minimal.yaml
s=$(sky launch --cloud gcp -d -c ${CLUSTER_NAME} examples/minimal.yaml)
echo $s
echo $s | grep "Job ID: 3"
sky queue ${CLUSTER_NAME}

# sky stop + sky start + sky exec
conda activate sky-back-compat-master
rm -r  ~/.sky/wheels || true
sky launch --cloud gcp -y -c ${CLUSTER_NAME}-2 examples/minimal.yaml
conda activate sky-back-compat-current
rm -r  ~/.sky/wheels || true
sky stop -y ${CLUSTER_NAME}-2
sky start -y ${CLUSTER_NAME}-2
s=$(sky exec --cloud gcp -d ${CLUSTER_NAME}-2 examples/minimal.yaml)
echo $s
echo $s | grep "Job ID: 2"

# `sky autostop` + `sky status -r`
conda activate sky-back-compat-master
rm -r  ~/.sky/wheels || true
sky launch --cloud gcp -y -c ${CLUSTER_NAME}-3 examples/minimal.yaml
conda activate sky-back-compat-current
rm -r  ~/.sky/wheels || true
sky autostop -y -i0 ${CLUSTER_NAME}-3
sleep 100
sky status -r | grep ${CLUSTER_NAME}-3 | grep STOPPED


# (1 node) sky launch + sky exec + sky queue + sky logs
conda activate sky-back-compat-master
rm -r  ~/.sky/wheels || true
sky launch --cloud gcp -y -c ${CLUSTER_NAME}-4 examples/minimal.yaml
sky stop -y ${CLUSTER_NAME}-4
conda activate sky-back-compat-current
rm -r  ~/.sky/wheels || true
sky launch --cloud gcp -y -c ${CLUSTER_NAME}-4 examples/minimal.yaml
sky queue ${CLUSTER_NAME}-4
sky logs ${CLUSTER_NAME}-4 1 --status
sky logs ${CLUSTER_NAME}-4 2 --status
sky logs ${CLUSTER_NAME}-4 1
sky logs ${CLUSTER_NAME}-4 2

# (1 node) sky start + sky exec + sky queue + sky logs
conda activate sky-back-compat-master
rm -r  ~/.sky/wheels || true
sky launch --cloud gcp -y -c ${CLUSTER_NAME}-5 examples/minimal.yaml
sky stop -y ${CLUSTER_NAME}-5
conda activate sky-back-compat-current
rm -r  ~/.sky/wheels || true
sky start -y ${CLUSTER_NAME}-5
sky queue ${CLUSTER_NAME}-5
sky logs ${CLUSTER_NAME}-5 1 --status
sky logs ${CLUSTER_NAME}-5 1
sky launch --cloud gcp -y -c ${CLUSTER_NAME}-5 examples/minimal.yaml
sky queue ${CLUSTER_NAME}-5
sky logs ${CLUSTER_NAME}-5 2 --status
sky logs ${CLUSTER_NAME}-5 2

# (2 nodes) sky launch + sky exec + sky queue + sky logs
conda activate sky-back-compat-master
rm -r  ~/.sky/wheels || true
sky launch --cloud gcp -y -c ${CLUSTER_NAME}-6 examples/multi_hostname.yaml
sky stop -y ${CLUSTER_NAME}-6
conda activate sky-back-compat-current
rm -r  ~/.sky/wheels || true
sky start -y ${CLUSTER_NAME}-6
sky queue ${CLUSTER_NAME}-6
sky logs ${CLUSTER_NAME}-6 1 --status
sky logs ${CLUSTER_NAME}-6 1
sky exec --cloud gcp ${CLUSTER_NAME}-6 examples/multi_hostname.yaml
sky queue ${CLUSTER_NAME}-6
sky logs ${CLUSTER_NAME}-6 2 --status
sky logs ${CLUSTER_NAME}-6 2
