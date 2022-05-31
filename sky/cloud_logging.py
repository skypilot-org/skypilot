"""Loki logging"""
import requests
import json
import datetime
import pytz
from sky.utils import base_utils
import re
import traceback

LOG_URL = 'https://178762:eyJrIjoiNzhlODNmOGQ1ZjUwOGJmYjE4NDI3YTMzNzFmMWMxZDc0YzA3ZmVlZiIsIm4iOiJhZHNmZHNhIiwiaWQiOjYxNTA0Nn0=@logs-prod3.grafana.net/api/prom/push'  # pylint: disable=line-too-long


def _make_labels_str(d):
    dict_str = '{'
    for e in d:
        dict_str += f'{e}="{d[e]}",'
    dict_str = dict_str[:-1] + '}'
    return dict_str


def _send_message(labels, msg):
    curr_datetime = datetime.datetime.now(pytz.timezone('US/Eastern'))
    curr_datetime = curr_datetime.isoformat('T')

    labels['host'] = base_utils.get_user()
    labels_str = _make_labels_str(labels)

    headers = {'Content-type': 'application/json'}
    payload = {
        'streams': [{
            'labels': labels_str,
            'entries': [{
                'ts': curr_datetime,
                'line': msg
            }]
        }]
    }
    payload = json.dumps(payload)
    response = requests.post(LOG_URL, data=payload, headers=headers)
    if response.status_code != 204:
        print('[WARNING] LOKI LOGGING FAILED')


def send_cli_cmd():
    """
    Upload current CLI command to Loki
    """
    cmd = base_utils.get_pretty_entry_point()
    labels = {'type': 'cli-cmd'}
    _send_message(labels, cmd)


def _clean_yaml(yaml_info):
    """
    Remove sensitive information from user YAML
    """
    cleaned_yaml_info = []

    redact = False
    redact_type = 'None'
    for line in yaml_info:
        if len(line) > 1 and line[0].strip() == line[0]:
            redact = False
        if line[0:5] == 'setup':
            redact = True
            redact_type = 'SETUP'
        if line[0:3] == 'run':
            redact = True
            redact_type = 'RUN'

        if redact:
            line = f'REDACTED {redact_type} CODE\n'
        line = re.sub('#.*', '# REDACTED COMMENT', line)
        cleaned_yaml_info.append(line)
    return cleaned_yaml_info


def send_yaml(yaml_path, determined=False):
    """
    Upload safe contents of YAML file to Loki
    """
    with open(yaml_path, 'r') as f:
        yaml_info = f.readlines()
        yaml_info = _clean_yaml(yaml_info)
        yaml_info = ''.join(yaml_info)
        type_label = 'user-yaml' if not determined else 'determined-yaml'
        labels = {'type': type_label}
        _send_message(labels, yaml_info)


def send_trace():
    """
    Upload stack trace for an exception
    """
    trace = traceback.format_exc()
    labels = {'type': 'stack-trace'}
    _send_message(labels, trace)
