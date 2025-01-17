"""Cudo catalog helper."""

cudo_gpu_model = {
    'NVIDIA V100': 'V100',
    'NVIDIA A40': 'A40',
    'RTX 3080': 'RTX3080',
    'RTX A4000': 'RTXA4000',
    'RTX A4500': 'RTXA4500',
    'RTX A5000': 'RTXA5000',
    'RTX A6000': 'RTXA6000',
}

cudo_gpu_mem = {
    'RTX3080': 12,
    'A40': 48,
    'RTXA4000': 16,
    'RTXA4500': 20,
    'RTXA5000': 24,
    'RTXA6000': 48,
    'V100': 16,
}

machine_specs = [
    # Low
    {
        'vcpu': 2,
        'mem': 4,
        'gpu': 1,
    },
    {
        'vcpu': 4,
        'mem': 8,
        'gpu': 1,
    },
    {
        'vcpu': 8,
        'mem': 16,
        'gpu': 2,
    },
    {
        'vcpu': 16,
        'mem': 32,
        'gpu': 2,
    },
    {
        'vcpu': 32,
        'mem': 64,
        'gpu': 4,
    },
    {
        'vcpu': 64,
        'mem': 128,
        'gpu': 8,
    },
    # Mid
    {
        'vcpu': 96,
        'mem': 192,
        'gpu': 8
    },
    {
        'vcpu': 48,
        'mem': 96,
        'gpu': 4
    },
    {
        'vcpu': 24,
        'mem': 48,
        'gpu': 2
    },
    {
        'vcpu': 12,
        'mem': 24,
        'gpu': 1
    },
    # Hi
    {
        'vcpu': 96,
        'mem': 192,
        'gpu': 4
    },
    {
        'vcpu': 48,
        'mem': 96,
        'gpu': 2
    },
    {
        'vcpu': 24,
        'mem': 48,
        'gpu': 1
    },
]


def cudo_gpu_to_skypilot_gpu(model):
    if model in cudo_gpu_model:
        return cudo_gpu_model[model]
    else:
        return model


def skypilot_gpu_to_cudo_gpu(model):
    for key, value in cudo_gpu_model.items():
        if value == model:
            return key
    return model


def gpu_exists(model):
    if model in cudo_gpu_model:
        return True
    return False
