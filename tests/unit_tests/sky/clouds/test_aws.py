"""Tests the inner loop of repeatedly querying instance status.

Used by spot controller. This test prevents #2668 from regressing.
"""

import os

import memory_profiler

from sky.provision.aws import instance

regions = ['us-east-1', 'us-east-2', 'us-west-1', 'us-west-2']


def test_aws_resources_memory_leakage():
    memory_usage_before = memory_profiler.memory_usage(os.getpid(),
                                                       interval=0.1,
                                                       timeout=1)[0]
    for i in range(int(1e6)):
        instance._default_ec2_resource(regions[i % len(regions)])
        if i % int(1e3) == 0:
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
