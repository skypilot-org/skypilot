#!/bin/bash
set -ev

source ~/.bashrc 
CLUSTER_NAME="test-back-compat-$USER"
source $(conda info --base 2> /dev/null)/etc/profile.d/conda.sh

git clone https://github.com/skypilot-org/skypilot.git sky-master || true


# Create environment for compatibility tests
mamba > /dev/null 2>&1 || conda install -n base -c conda-forge mamba -y
mamba init
source ~/.bashrc
mamba env list | grep sky-back-compat-master || mamba create -n sky-back-compat-master -y python=3.9

mamba activate sky-back-compat-master
mamba install -c conda-forge google-cloud-sdk -y
rm -r  ~/.sky/wheels || true
cd sky-master
git pull origin master
pip install -e ".[all]"
cd -

mamba env list | grep sky-back-compat-current || mamba create -n sky-back-compat-current -y --clone sky-back-compat-master
mamba activate sky-back-compat-current
rm -r  ~/.sky/wheels || true
pip uninstall -y skypilot
pip install -e ".[all]"


# exec + launch
mamba activate sky-back-compat-master
rm -r  ~/.sky/wheels || true
which sky
sky launch --cloud gcp -y -c ${CLUSTER_NAME} examples/minimal.yaml

mamba activate sky-back-compat-current
rm -r  ~/.sky/wheels || true
sky exec --cloud gcp ${CLUSTER_NAME} examples/minimal.yaml
s=$(sky launch --cloud gcp -d -c ${CLUSTER_NAME} examples/minimal.yaml)
echo $s
echo $s | grep "Job ID: 3" || exit 1
sky queue ${CLUSTER_NAME}

# sky stop + sky start + sky exec
mamba activate sky-back-compat-master
rm -r  ~/.sky/wheels || true
sky launch --cloud gcp -y -c ${CLUSTER_NAME}-2 examples/minimal.yaml
mamba activate sky-back-compat-current
rm -r  ~/.sky/wheels || true
sky stop -y ${CLUSTER_NAME}-2
sky start -y ${CLUSTER_NAME}-2
s=$(sky exec --cloud gcp -d ${CLUSTER_NAME}-2 examples/minimal.yaml)
echo $s
echo $s | grep "Job ID: 2" || exit 1

# `sky autostop` + `sky status -r`
mamba activate sky-back-compat-master
rm -r  ~/.sky/wheels || true
sky launch --cloud gcp -y -c ${CLUSTER_NAME}-3 examples/minimal.yaml
mamba activate sky-back-compat-current
rm -r  ~/.sky/wheels || true
sky autostop -y -i0 ${CLUSTER_NAME}-3
sleep 100
sky status -r | grep ${CLUSTER_NAME}-3 | grep STOPPED || exit 1


# (1 node) sky launch + sky exec + sky queue + sky logs
mamba activate sky-back-compat-master
rm -r  ~/.sky/wheels || true
sky launch --cloud gcp -y -c ${CLUSTER_NAME}-4 examples/minimal.yaml
sky stop -y ${CLUSTER_NAME}-4
mamba activate sky-back-compat-current
rm -r  ~/.sky/wheels || true
sky launch --cloud gcp -y -c ${CLUSTER_NAME}-4 examples/minimal.yaml
sky queue ${CLUSTER_NAME}-4
sky logs ${CLUSTER_NAME}-4 1 --status
sky logs ${CLUSTER_NAME}-4 2 --status
sky logs ${CLUSTER_NAME}-4 1
sky logs ${CLUSTER_NAME}-4 2

# (1 node) sky start + sky exec + sky queue + sky logs
mamba activate sky-back-compat-master
rm -r  ~/.sky/wheels || true
sky launch --cloud gcp -y -c ${CLUSTER_NAME}-5 examples/minimal.yaml
sky stop -y ${CLUSTER_NAME}-5
mamba activate sky-back-compat-current
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
mamba activate sky-back-compat-master
rm -r  ~/.sky/wheels || true
sky launch --cloud gcp -y -c ${CLUSTER_NAME}-6 examples/multi_hostname.yaml
sky stop -y ${CLUSTER_NAME}-6
mamba activate sky-back-compat-current
rm -r  ~/.sky/wheels || true
sky start -y ${CLUSTER_NAME}-6
sky queue ${CLUSTER_NAME}-6
sky logs ${CLUSTER_NAME}-6 1 --status
sky logs ${CLUSTER_NAME}-6 1
sky exec --cloud gcp ${CLUSTER_NAME}-6 examples/multi_hostname.yaml
sky queue ${CLUSTER_NAME}-6
sky logs ${CLUSTER_NAME}-6 2 --status
sky logs ${CLUSTER_NAME}-6 2

sky down ${CLUSTER_NAME}* -y
