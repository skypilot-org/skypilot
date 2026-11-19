import { jobRunTimeRange } from '@/components/TelemetrySection';

describe('jobRunTimeRange', () => {
  it('returns epoch-ms strings for a valid start/end window', () => {
    expect(jobRunTimeRange(1000, 1002)).toEqual({
      from: '1000000',
      to: '1002000',
    });
  });

  it('returns null when either bound is missing', () => {
    expect(jobRunTimeRange(null, 1002)).toBeNull();
    expect(jobRunTimeRange(1000, null)).toBeNull();
    expect(jobRunTimeRange(undefined, undefined)).toBeNull();
  });

  it('returns null when the window is empty or inverted', () => {
    expect(jobRunTimeRange(1002, 1000)).toBeNull();
    expect(jobRunTimeRange(1000, 1000)).toBeNull();
  });
});
