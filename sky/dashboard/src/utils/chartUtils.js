/**
 * Chart utilities for time-series data with bucketing support.
 *
 * These utilities help format tooltips and labels for charts that use
 * time bucketing (where each data point represents an aggregated value
 * over a time range rather than a single instant).
 */

/**
 * Format a timestamp for display in chart tooltips and labels.
 *
 * @param {number|Date|string} timestamp - The timestamp to format
 * @param {Object} [options] - Formatting options
 * @param {boolean} [options.includeTime=false] - Whether to include time (HH:MM)
 * @param {boolean} [options.shortMonth=true] - Use abbreviated month names
 * @returns {string} Formatted date string (e.g., "May 21" or "May 21, 10:30")
 */
export function formatTimestamp(timestamp, options = {}) {
  const { includeTime = false, shortMonth = true } = options;

  if (timestamp == null) {
    return 'Invalid date';
  }

  const date = timestamp instanceof Date ? timestamp : new Date(timestamp);

  if (isNaN(date.getTime())) {
    return 'Invalid date';
  }

  const monthFormat = shortMonth ? 'short' : 'long';
  const dateStr = date.toLocaleDateString('en-US', {
    month: monthFormat,
    day: 'numeric',
  });

  if (includeTime) {
    const timeStr = date.toLocaleTimeString('en-US', {
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    });
    return `${dateStr}, ${timeStr}`;
  }

  return dateStr;
}

/**
 * Format a time range for bucketed data tooltips.
 *
 * When displaying time-series data that uses bucketing (each point represents
 * aggregated data over a time range), this function formats the range clearly
 * to avoid user confusion about what each data point represents.
 *
 * @param {number|Date|string} startTime - Start of the time bucket
 * @param {number|Date|string} endTime - End of the time bucket
 * @param {Object} [options] - Formatting options
 * @param {boolean} [options.includeTime=false] - Include time if range is within a day
 * @returns {string} Formatted range (e.g., "May 21 to May 26" or "May 21, 10:00 to 14:00")
 */
export function formatTimeRange(startTime, endTime, options = {}) {
  const { includeTime = false } = options;

  if (startTime == null || endTime == null) {
    return 'Invalid date range';
  }

  const start = startTime instanceof Date ? startTime : new Date(startTime);
  const end = endTime instanceof Date ? endTime : new Date(endTime);

  if (isNaN(start.getTime()) || isNaN(end.getTime())) {
    return 'Invalid date range';
  }

  const isSameDay =
    start.getFullYear() === end.getFullYear() &&
    start.getMonth() === end.getMonth() &&
    start.getDate() === end.getDate();

  if (isSameDay) {
    const dateStr = formatTimestamp(start, { includeTime: false });
    if (includeTime) {
      const startTime = start.toLocaleTimeString('en-US', {
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      });
      const endTime = end.toLocaleTimeString('en-US', {
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      });
      return `${dateStr}, ${startTime} to ${endTime}`;
    }
    return dateStr;
  }

  const startStr = formatTimestamp(start, { includeTime });
  const endStr = formatTimestamp(end, { includeTime });
  return `${startStr} to ${endStr}`;
}

/**
 * Create a tooltip label for bucketed time-series data.
 *
 * This is the main helper for creating clear, unambiguous tooltip labels
 * that show both the time range and the value for bucketed data points.
 *
 * @param {Object} params - Tooltip parameters
 * @param {number|Date|string} params.startTime - Start of the time bucket
 * @param {number|Date|string} params.endTime - End of the time bucket
 * @param {number} params.value - The aggregated value for this bucket
 * @param {string} [params.label] - Label for the value (e.g., "failures", "events")
 * @param {string} [params.valuePrefix] - Prefix for the value (e.g., "$", "")
 * @param {string} [params.valueSuffix] - Suffix for the value (e.g., "%", " GB")
 * @param {Object} [params.formatOptions] - Options passed to formatTimeRange
 * @returns {string} Formatted tooltip label (e.g., "May 21 to May 26 failures: 42")
 */
export function createBucketedTooltipLabel(params) {
  const {
    startTime,
    endTime,
    value,
    label = '',
    valuePrefix = '',
    valueSuffix = '',
    formatOptions = {},
  } = params;

  const timeRange = formatTimeRange(startTime, endTime, formatOptions);
  const formattedValue = `${valuePrefix}${value}${valueSuffix}`;

  if (label) {
    return `${timeRange} ${label}: ${formattedValue}`;
  }
  return `${timeRange}: ${formattedValue}`;
}

/**
 * Create Chart.js tooltip callbacks for bucketed time-series data.
 *
 * Returns callback functions compatible with Chart.js tooltip configuration
 * that properly display time ranges for bucketed data.
 *
 * @param {Object} options - Configuration options
 * @param {Function} options.getStartTime - Function to get start time from data point (dataPoint, index) => timestamp
 * @param {Function} options.getEndTime - Function to get end time from data point (dataPoint, index) => timestamp
 * @param {string} [options.label] - Label for values (e.g., "failures")
 * @param {string} [options.valuePrefix] - Prefix for values
 * @param {string} [options.valueSuffix] - Suffix for values
 * @param {Object} [options.formatOptions] - Options for time formatting
 * @returns {Object} Chart.js tooltip callbacks object
 */
export function createBucketedTooltipCallbacks(options) {
  const {
    getStartTime,
    getEndTime,
    label = '',
    valuePrefix = '',
    valueSuffix = '',
    formatOptions = {},
  } = options;

  return {
    title: (tooltipItems) => {
      if (!tooltipItems.length) return '';
      const item = tooltipItems[0];
      const dataPoint = item.raw;
      const index = item.dataIndex;

      const startTime = getStartTime(dataPoint, index);
      const endTime = getEndTime(dataPoint, index);

      return formatTimeRange(startTime, endTime, formatOptions);
    },
    label: (tooltipItem) => {
      const value = tooltipItem.formattedValue || tooltipItem.raw?.y || tooltipItem.raw;
      const datasetLabel = tooltipItem.dataset.label || label;
      const formattedValue = `${valuePrefix}${value}${valueSuffix}`;

      if (datasetLabel) {
        return `${datasetLabel}: ${formattedValue}`;
      }
      return formattedValue;
    },
  };
}

/**
 * Calculate appropriate time bucket size based on the time range.
 *
 * Helps determine sensible bucket sizes to avoid overcrowding or
 * under-sampling data points on time-series charts.
 *
 * @param {number|Date|string} startTime - Start of the overall time range
 * @param {number|Date|string} endTime - End of the overall time range
 * @param {number} [maxBuckets=50] - Maximum number of buckets to display
 * @returns {Object} Bucket configuration { bucketMs, bucketLabel }
 */
export function calculateTimeBucketSize(startTime, endTime, maxBuckets = 50) {
  const start = new Date(startTime).getTime();
  const end = new Date(endTime).getTime();
  const rangeMs = end - start;

  const MINUTE = 60 * 1000;
  const HOUR = 60 * MINUTE;
  const DAY = 24 * HOUR;
  const WEEK = 7 * DAY;

  const bucketSizes = [
    { ms: 5 * MINUTE, label: '5 minutes' },
    { ms: 15 * MINUTE, label: '15 minutes' },
    { ms: 30 * MINUTE, label: '30 minutes' },
    { ms: HOUR, label: '1 hour' },
    { ms: 2 * HOUR, label: '2 hours' },
    { ms: 4 * HOUR, label: '4 hours' },
    { ms: 6 * HOUR, label: '6 hours' },
    { ms: 12 * HOUR, label: '12 hours' },
    { ms: DAY, label: '1 day' },
    { ms: 2 * DAY, label: '2 days' },
    { ms: 3 * DAY, label: '3 days' },
    { ms: WEEK, label: '1 week' },
    { ms: 2 * WEEK, label: '2 weeks' },
    { ms: 4 * WEEK, label: '4 weeks' },
  ];

  for (const bucket of bucketSizes) {
    if (rangeMs / bucket.ms <= maxBuckets) {
      return { bucketMs: bucket.ms, bucketLabel: bucket.label };
    }
  }

  return {
    bucketMs: Math.ceil(rangeMs / maxBuckets),
    bucketLabel: 'auto',
  };
}

/**
 * Aggregate data points into time buckets.
 *
 * Takes raw time-series data and aggregates it into buckets, returning
 * data suitable for charting with proper time range information.
 *
 * @param {Array} dataPoints - Array of { timestamp, value } objects
 * @param {number} bucketMs - Bucket size in milliseconds
 * @param {string} [aggregation='sum'] - Aggregation method: 'sum', 'avg', 'max', 'min', 'count'
 * @returns {Array} Bucketed data points with { startTime, endTime, value }
 */
export function aggregateIntoBuckets(dataPoints, bucketMs, aggregation = 'sum') {
  if (!dataPoints.length) return [];

  const sorted = [...dataPoints].sort(
    (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()
  );

  const firstTime = new Date(sorted[0].timestamp).getTime();
  const lastTime = new Date(sorted[sorted.length - 1].timestamp).getTime();

  const buckets = new Map();

  for (const point of sorted) {
    const pointTime = new Date(point.timestamp).getTime();
    const bucketStart = Math.floor((pointTime - firstTime) / bucketMs) * bucketMs + firstTime;

    if (!buckets.has(bucketStart)) {
      buckets.set(bucketStart, []);
    }
    buckets.get(bucketStart).push(point.value);
  }

  const result = [];
  for (const [bucketStart, values] of buckets) {
    let aggregatedValue;
    switch (aggregation) {
      case 'sum':
        aggregatedValue = values.reduce((a, b) => a + b, 0);
        break;
      case 'avg':
        aggregatedValue = values.reduce((a, b) => a + b, 0) / values.length;
        break;
      case 'max':
        aggregatedValue = Math.max(...values);
        break;
      case 'min':
        aggregatedValue = Math.min(...values);
        break;
      case 'count':
        aggregatedValue = values.length;
        break;
      default:
        aggregatedValue = values.reduce((a, b) => a + b, 0);
    }

    result.push({
      startTime: bucketStart,
      endTime: Math.min(bucketStart + bucketMs, lastTime),
      value: aggregatedValue,
    });
  }

  return result.sort((a, b) => a.startTime - b.startTime);
}
