"""Cudo catalog helper."""

cudo_gpu_model = {
    'H100 NVL': 'H100',
    'H100 SXM': 'H100-SXM',
    'L40S (compute mode)': 'L40S',
    'L40S (graphics mode)': 'L40S',
    'A40 (compute mode)': 'A40',
    'A40 (graphics mode)': 'A40',
    'RTX A5000': 'RTXA5000',
    'RTX A6000': 'RTXA6000',
    'A100 80GB PCIe': 'A100',
    'A800 PCIe': 'A800',
    'V100': 'V100',
}

cudo_gpu_mem = {
    'H100': 94,
    'H100-SXM': 80,
    'L40S': 48,
    'A40': 48,
    'RTXA5000': 24,
    'RTXA6000': 48,
    'A100': 80,
    'A800': 80,
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
