import { formatCpu, formatMemory } from '@/utils/resourceUtils';

describe('formatCpu', () => {
  it('returns the integer count below the 10000 threshold', () => {
    expect(formatCpu(9999)).toBe('9999');
    expect(formatCpu(96)).toBe('96');
    expect(formatCpu(0)).toBe('0');
  });

  it('rounds fractional counts below the threshold', () => {
    expect(formatCpu(8.6)).toBe('9');
  });

  it('switches to one-decimal k at 10000', () => {
    expect(formatCpu(10000)).toBe('10.0k');
    expect(formatCpu(44202)).toBe('44.2k');
    expect(formatCpu(11520)).toBe('11.5k');
  });

  it('returns - for null and undefined', () => {
    expect(formatCpu(null)).toBe('-');
    expect(formatCpu(undefined)).toBe('-');
  });
});

describe('formatMemory', () => {
  it('returns the integer GB count below the 10000 GB threshold', () => {
    expect(formatMemory(9999.4)).toBe('9999 GB');
    expect(formatMemory(16)).toBe('16 GB');
    expect(formatMemory(0)).toBe('0 GB');
  });

  it('switches to one-decimal TB at 10000 GB', () => {
    expect(formatMemory(10000)).toBe('9.8 TB');
    expect(formatMemory(681745)).toBe('665.8 TB');
    expect(formatMemory(240573)).toBe('234.9 TB');
    expect(formatMemory(67105)).toBe('65.5 TB');
  });

  it('returns - for null and undefined', () => {
    expect(formatMemory(null)).toBe('-');
    expect(formatMemory(undefined)).toBe('-');
  });
});
