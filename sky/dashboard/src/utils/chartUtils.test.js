/**
 * Tests for chart utilities - time-bucketed data formatting
 */

import {
  formatTimestamp,
  formatTimeRange,
  createBucketedTooltipLabel,
  createBucketedTooltipCallbacks,
  calculateTimeBucketSize,
  aggregateIntoBuckets,
} from './chartUtils';

describe('chartUtils', () => {
  describe('formatTimestamp', () => {
    test('formats date correctly without time', () => {
      const date = new Date('2024-05-21T10:30:00Z');
      const result = formatTimestamp(date);
      expect(result).toMatch(/May 21/);
    });

    test('formats date correctly with time', () => {
      const date = new Date('2024-05-21T10:30:00Z');
      const result = formatTimestamp(date, { includeTime: true });
      expect(result).toMatch(/May 21/);
      expect(result).toMatch(/:/);
    });

    test('handles timestamps as numbers', () => {
      const timestamp = new Date('2024-05-21T10:30:00Z').getTime();
      const result = formatTimestamp(timestamp);
      expect(result).toMatch(/May 21/);
    });

    test('handles invalid dates', () => {
      const result = formatTimestamp('invalid');
      expect(result).toBe('Invalid date');
    });

    test('handles null/undefined', () => {
      expect(formatTimestamp(null)).toBe('Invalid date');
      expect(formatTimestamp(undefined)).toBe('Invalid date');
    });
  });

  describe('formatTimeRange', () => {
    test('formats multi-day range correctly', () => {
      const start = new Date('2024-05-21T00:00:00Z');
      const end = new Date('2024-05-26T00:00:00Z');
      const result = formatTimeRange(start, end);
      expect(result).toMatch(/May 21.*to.*May 26/);
    });

    test('formats same-day range correctly', () => {
      const start = new Date('2024-05-21T10:00:00Z');
      const end = new Date('2024-05-21T14:00:00Z');
      const result = formatTimeRange(start, end);
      expect(result).toMatch(/May 21/);
      expect(result).not.toMatch(/to/);
    });

    test('formats same-day range with time correctly', () => {
      const start = new Date('2024-05-21T10:00:00Z');
      const end = new Date('2024-05-21T14:00:00Z');
      const result = formatTimeRange(start, end, { includeTime: true });
      expect(result).toMatch(/May 21/);
      expect(result).toMatch(/to/);
    });

    test('handles invalid dates', () => {
      const result = formatTimeRange('invalid', 'invalid');
      expect(result).toBe('Invalid date range');
    });
  });

  describe('createBucketedTooltipLabel', () => {
    test('creates label with value and label', () => {
      const result = createBucketedTooltipLabel({
        startTime: new Date('2024-05-21'),
        endTime: new Date('2024-05-26'),
        value: 42,
        label: 'failures',
      });
      expect(result).toMatch(/May 21.*to.*May 26.*failures.*42/);
    });

    test('creates label without label text', () => {
      const result = createBucketedTooltipLabel({
        startTime: new Date('2024-05-21'),
        endTime: new Date('2024-05-26'),
        value: 42,
      });
      expect(result).toMatch(/May 21.*to.*May 26.*:.*42/);
    });

    test('creates label with prefix and suffix', () => {
      const result = createBucketedTooltipLabel({
        startTime: new Date('2024-05-21'),
        endTime: new Date('2024-05-26'),
        value: 42,
        valuePrefix: '$',
        valueSuffix: 'K',
      });
      expect(result).toContain('$42K');
    });
  });

  describe('createBucketedTooltipCallbacks', () => {
    test('creates valid callback object', () => {
      const callbacks = createBucketedTooltipCallbacks({
        getStartTime: (point) => point.startTime,
        getEndTime: (point) => point.endTime,
        label: 'failures',
      });

      expect(callbacks).toHaveProperty('title');
      expect(callbacks).toHaveProperty('label');
      expect(typeof callbacks.title).toBe('function');
      expect(typeof callbacks.label).toBe('function');
    });

    test('title callback formats time range', () => {
      const callbacks = createBucketedTooltipCallbacks({
        getStartTime: (point) => point.startTime,
        getEndTime: (point) => point.endTime,
      });

      const mockTooltipItems = [
        {
          raw: {
            startTime: new Date('2024-05-21').getTime(),
            endTime: new Date('2024-05-26').getTime(),
          },
          dataIndex: 0,
        },
      ];

      const title = callbacks.title(mockTooltipItems);
      expect(title).toMatch(/May 21.*to.*May 26/);
    });

    test('title callback handles empty items', () => {
      const callbacks = createBucketedTooltipCallbacks({
        getStartTime: (point) => point.startTime,
        getEndTime: (point) => point.endTime,
      });

      const title = callbacks.title([]);
      expect(title).toBe('');
    });
  });

  describe('calculateTimeBucketSize', () => {
    test('calculates bucket for 1 hour range', () => {
      const start = new Date('2024-05-21T10:00:00Z');
      const end = new Date('2024-05-21T11:00:00Z');
      const result = calculateTimeBucketSize(start, end, 20);
      expect(result.bucketMs).toBeLessThanOrEqual(60 * 60 * 1000);
      expect(result.bucketLabel).toBeDefined();
    });

    test('calculates bucket for 7 day range', () => {
      const start = new Date('2024-05-21');
      const end = new Date('2024-05-28');
      const result = calculateTimeBucketSize(start, end, 20);
      expect(result.bucketMs).toBeGreaterThan(60 * 60 * 1000);
      expect(result.bucketLabel).toBeDefined();
    });

    test('calculates bucket for 30 day range', () => {
      const start = new Date('2024-05-01');
      const end = new Date('2024-05-31');
      const result = calculateTimeBucketSize(start, end, 20);
      expect(result.bucketMs).toBeGreaterThan(24 * 60 * 60 * 1000);
      expect(result.bucketLabel).toBeDefined();
    });
  });

  describe('aggregateIntoBuckets', () => {
    test('aggregates data into buckets with sum', () => {
      const dataPoints = [
        { timestamp: new Date('2024-05-21T10:00:00Z'), value: 5 },
        { timestamp: new Date('2024-05-21T10:30:00Z'), value: 3 },
        { timestamp: new Date('2024-05-21T12:00:00Z'), value: 7 },
      ];

      const bucketMs = 2 * 60 * 60 * 1000; // 2 hours
      const result = aggregateIntoBuckets(dataPoints, bucketMs, 'sum');

      expect(result.length).toBeGreaterThan(0);
      expect(result[0]).toHaveProperty('startTime');
      expect(result[0]).toHaveProperty('endTime');
      expect(result[0]).toHaveProperty('value');
    });

    test('handles empty data', () => {
      const result = aggregateIntoBuckets([], 3600000, 'sum');
      expect(result).toEqual([]);
    });

    test('aggregates with average', () => {
      const dataPoints = [
        { timestamp: new Date('2024-05-21T10:00:00Z'), value: 10 },
        { timestamp: new Date('2024-05-21T10:30:00Z'), value: 20 },
      ];

      const bucketMs = 2 * 60 * 60 * 1000;
      const result = aggregateIntoBuckets(dataPoints, bucketMs, 'avg');

      expect(result[0].value).toBe(15);
    });

    test('aggregates with count', () => {
      const dataPoints = [
        { timestamp: new Date('2024-05-21T10:00:00Z'), value: 10 },
        { timestamp: new Date('2024-05-21T10:30:00Z'), value: 20 },
        { timestamp: new Date('2024-05-21T11:00:00Z'), value: 30 },
      ];

      const bucketMs = 2 * 60 * 60 * 1000;
      const result = aggregateIntoBuckets(dataPoints, bucketMs, 'count');

      expect(result[0].value).toBe(3);
    });

    test('aggregates with max', () => {
      const dataPoints = [
        { timestamp: new Date('2024-05-21T10:00:00Z'), value: 10 },
        { timestamp: new Date('2024-05-21T10:30:00Z'), value: 50 },
        { timestamp: new Date('2024-05-21T11:00:00Z'), value: 30 },
      ];

      const bucketMs = 2 * 60 * 60 * 1000;
      const result = aggregateIntoBuckets(dataPoints, bucketMs, 'max');

      expect(result[0].value).toBe(50);
    });

    test('aggregates with min', () => {
      const dataPoints = [
        { timestamp: new Date('2024-05-21T10:00:00Z'), value: 10 },
        { timestamp: new Date('2024-05-21T10:30:00Z'), value: 50 },
        { timestamp: new Date('2024-05-21T11:00:00Z'), value: 30 },
      ];

      const bucketMs = 2 * 60 * 60 * 1000;
      const result = aggregateIntoBuckets(dataPoints, bucketMs, 'min');

      expect(result[0].value).toBe(10);
    });
  });
});
