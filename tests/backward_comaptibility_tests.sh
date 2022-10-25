#!/bin/bash
set -ev

need_launch=${1:-0}
start_from=${2:-0}

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
if [ "$start_from" -le 1 ]; then
mamba activate sky-back-compat-master
rm -r  ~/.sky/wheels || true
which sky
sky launch --cloud gcp -y -c ${CLUSTER_NAME} examples/minimal.yaml

mamba activate sky-back-compat-current
rm -r  ~/.sky/wheels || true
if [ "$need_launch" -eq "1" ]; then
  sky launch --cloud gcp -y -c ${CLUSTER_NAME}
fi
sky exec --cloud gcp ${CLUSTER_NAME} examples/minimal.yaml
s=$(sky launch --cloud gcp -d -c ${CLUSTER_NAME} examples/minimal.yaml)
echo $s
# remove color and find the job id
echo $s | sed -r "s/\x1B\[([0-9]{1,3}(;[0-9]{1,2})?)?[mGK]//g" | grep "Job ID: 3" || exit 1
sky queue ${CLUSTER_NAME}
fi

# sky stop + sky start + sky exec
if [ "$start_from" -le 2 ]; then
mamba activate sky-back-compat-master
rm -r  ~/.sky/wheels || true
sky launch --cloud gcp -y -c ${CLUSTER_NAME}-2 examples/minimal.yaml
mamba activate sky-back-compat-current
rm -r  ~/.sky/wheels || true
sky stop -y ${CLUSTER_NAME}-2
sky start -y ${CLUSTER_NAME}-2
s=$(sky exec --cloud gcp -d ${CLUSTER_NAME}-2 examples/minimal.yaml)
echo $s
echo $s | sed -r "s/\x1B\[([0-9]{1,3}(;[0-9]{1,2})?)?[mGK]//g" | grep "Job ID: 2" || exit 1
fi

# `sky autostop` + `sky status -r`
if [ "$start_from" -le 3 ]; then
mamba activate sky-back-compat-master
rm -r  ~/.sky/wheels || true
sky launch --cloud gcp -y -c ${CLUSTER_NAME}-3 examples/minimal.yaml
mamba activate sky-back-compat-current
rm -r  ~/.sky/wheels || true
sky autostop -y -i0 ${CLUSTER_NAME}-3
sleep 120
sky status -r | grep ${CLUSTER_NAME}-3 | grep STOPPED || exit 1
fi


# (1 node) sky launch + sky exec + sky queue + sky logs
if [ "$start_from" -le 4 ]; then
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
fi

# (1 node) sky start + sky exec + sky queue + sky logs
if [ "$start_form" -le 5 ]; then
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
fi

# (2 nodes) sky launch + sky exec + sky queue + sky logs
if [ "$start_from" -le 6 ]; then
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
fi

sky down ${CLUSTER_NAME}* -y
