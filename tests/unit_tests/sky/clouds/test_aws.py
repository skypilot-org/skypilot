import memory_profiler

from sky.provision.aws import instance

regions = ['us-east-1', 'us-east-2', 'us-west-1', 'us-west-2']


def test_aws_resources_memory_leakage():
    memory_usage_before = memory_profiler.memory_usage(-1,
                                                       interval=0.1,
                                                       timeout=1)[0]
    for i in range(int(1e4)):
        instance._default_ec2_resource(regions[i % len(regions)])
    mem_usage_after = memory_profiler.memory_usage(-1, interval=0.1,
                                                   timeout=1)[0]
    delta = mem_usage_after - memory_usage_before
    assert delta <= 40, (
        f'Function used {delta:.2f}MB which is more than the allowed 40MB')
