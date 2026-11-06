import { canonicalizeGpuName } from '@/utils/gpuUtils';

describe('canonicalizeGpuName', () => {
  it('maps GKE TPU accelerator labels to their generation', () => {
    expect(canonicalizeGpuName('tpu-v6e-slice')).toBe('TPU-v6e');
    expect(canonicalizeGpuName('tpu-v5p-slice')).toBe('TPU-v5p');
    expect(canonicalizeGpuName('tpu-v5-lite-podslice')).toBe('TPU-v5e');
    expect(canonicalizeGpuName('tpu-v5-lite-device')).toBe('TPU-v5e');
    expect(canonicalizeGpuName('tpu-v4-podslice')).toBe('TPU-v4');
  });

  it('matches TPU labels case-insensitively and ignores whitespace', () => {
    expect(canonicalizeGpuName(' TPU-V6E-SLICE ')).toBe('TPU-v6e');
  });

  it('falls back to the cleaned-up name for unknown TPU labels', () => {
    expect(canonicalizeGpuName('tpu-v9-something')).toBe('TPU-V9-SOMETHING');
  });

  it('still canonicalizes GPU names', () => {
    expect(canonicalizeGpuName('NVIDIA-H100-80GB-HBM3')).toBe('H100');
    expect(canonicalizeGpuName('nvidia-l4')).toBe('L4');
    expect(canonicalizeGpuName('')).toBe('Unknown');
  });
});
