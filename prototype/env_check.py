import subprocess
from typing import List

import google.auth

import sky


def _run_output(cmd):
    proc = subprocess.run(cmd, shell=True, check=True, stdout=subprocess.PIPE)
    return proc.stdout.decode('ascii')


def check_aws():
    try:
        output = _run_output('aws configure list')
    except subprocess.CalledProcessError:
        return False, 'AWS CLI not installed properly.'
    lines = output.split('\n')
    if len(lines) < 2:
        return False, 'AWS CLI output invalid.'
    access_key_ok = False
    secret_key_ok = False
    for line in lines[2:]:
        line = line.lstrip()
        if line.startswith('access_key'):
            if '<not set>' not in line:
                access_key_ok = True
        elif line.startswith('secret_key'):
            if '<not set>' not in line:
                secret_key_ok = True
    if access_key_ok and secret_key_ok:
        return True, None
    return False, 'AWS credentials not set. Run `aws configure`.'


def check_azure():
    try:
        output = _run_output('az account show --output=json')
    except subprocess.CalledProcessError:
        return False, 'AWS CLI not installed properly.'
    if output.startswith('{'):
        return True, None
    return False, 'Azure credentials not set. Run `az login`.'


def check_gcp():
    try:
        google.auth.default()
    except google.auth.exceptions.DefaultCredentialsError:
        return False, 'GCP credentials not set. Run `gcloud auth application-default login`.'
    return True, None


def get_available_clouds() -> List[sky.Cloud]:
    """Returns a list of clouds that the user has access to."""
    ret = []
    for cloud, check_fn in [(sky.AWS(), check_aws), (sky.Azure(), check_azure),
                            (sky.GCP(), check_gcp)]:
        ok, reason = check_fn()
        if ok:
            ret.append(cloud)
        else:
            print(cloud, reason)
    return ret


if __name__ == "__main__":
    print(get_available_clouds())
