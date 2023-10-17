"""Tests the inner loop of repeatedly querying instance status.

Used by spot controller. This test prevents #2668 from regressing.
"""

import math
import os

import memory_profiler

from sky.provision.aws import instance

aws_regions = ['us-east-1', 'us-east-2', 'us-west-1', 'us-west-2']


def test_aws_adaptor_resources_memory_leakage():
    memory_usage_before = memory_profiler.memory_usage(os.getpid(),
                                                       interval=0.1,
                                                       timeout=1)[0]
    total_num = int(1e3)
    for i in range(total_num):
        instance._default_ec2_resource(aws_regions[i % len(aws_regions)])
        if math.log10(i + 1).is_integer():
            print(i)
            mem_usage_after = memory_profiler.memory_usage(os.getpid(),
                                                           interval=0.1,
                                                           timeout=1)[0]
            delta = mem_usage_after - memory_usage_before
            print('memory usage:', delta)
            # 120MB is chosen by only creating 4 EC2 resources, and the memory
            # usage should not grow when creating more.
            assert delta <= 120, (
                f'Function used {delta:.2f}MB which is more than the '
                'allowed 120MB')
