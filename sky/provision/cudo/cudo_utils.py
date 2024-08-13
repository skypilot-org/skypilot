"""Cudo catalog helper."""

cudo_gpu_model = {
    '': '',
    # 'nvidia-a40': 'A40',
    'nvidia-a40-compute': 'A40',
    'nvidia-h100': 'H100',
    'nvidia-rtx-a4000': 'RTXA4000',
    'nvidia-rtx-a4500': 'RTXA4500',
    'nvidia-rtx-a5000': 'RTXA5000',
    'nvidia-rtx-a6000': 'RTXA6000',
    'nvidia-v100': 'V100',
}

cudo_gpu_mem = {
    'RTX3080': 12,
    'A40': 48,
    'RTXA4000': 16,
    'RTXA4500': 20,
    'RTXA5000': 24,
    'RTXA6000': 48,
    'V100': 16,
    'H100': 80,
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
