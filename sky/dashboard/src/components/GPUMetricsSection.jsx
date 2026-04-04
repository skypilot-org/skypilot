import React, { useState, useEffect, useMemo } from 'react';
import {
  ChevronDownIcon,
  ChevronRightIcon,
  ExternalLinkIcon,
} from 'lucide-react';
import { CustomTooltip as Tooltip } from '@/components/utils';
import { getGrafanaUrl, buildGrafanaUrl } from '@/utils/grafana';

// Grafana configuration constants
const GRAFANA_DASHBOARD_SLUG = 'skypilot-dcgm-gpu/skypilot-dcgm-gpu-metrics';
const GRAFANA_ORG_ID = '1';

// Time range presets for GPU metrics
const TIME_RANGE_PRESETS = [
  { label: '15m', value: '15m' },
  { label: '1h', value: '1h' },
  { label: '6h', value: '6h' },
  { label: '24h', value: '24h' },
  { label: '7d', value: '7d' },
];

// Duration multipliers for preset parsing
const DURATION_MS = { m: 60000, h: 3600000, d: 86400000 };

// GPU panels configuration
const GPU_PANELS = [
  { id: '1', title: 'GPU Utilization', keyPrefix: 'gpu-util' },
  { id: '2', title: 'GPU Memory Utilization', keyPrefix: 'gpu-memory' },
  { id: '3', title: 'GPU Temperature', keyPrefix: 'gpu-temp' },
  { id: '4', title: 'GPU Power Usage', keyPrefix: 'gpu-power' },
];

/**
 * Parse a duration preset string (e.g., '1h', '15m', '7d') to milliseconds.
 */
const parseDurationMs = (preset) => {
  const match = preset.match(/^(\d+)([mhd])$/);
  if (!match) return 3600000;
  return parseInt(match[1]) * (DURATION_MS[match[2]] || 3600000);
};

/**
 * Format a time range value for display.
 * Relative values like "now-1h" are shown as-is.
 * Absolute epoch ms values are formatted as readable dates.
 */
const formatTimeDisplay = (value) => {
  if (typeof value === 'string' && value.startsWith('now')) return value;
  const ts = parseInt(value);
  if (isNaN(ts)) return value;
  return new Date(ts).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  });
};

/**
 * Get epoch ms from a Date or number.
 */
const toEpochMs = (time) => {
  if (!time) return null;
  if (time instanceof Date) return time.getTime();
  return typeof time === 'number' ? time : null;
};

/**
 * Build Grafana panel URL with filters
 */
const buildGrafanaMetricsUrl = (panelId, clusterNameOnCloud, timeRange) => {
  const grafanaUrl = getGrafanaUrl();
  const params = new URLSearchParams({
    orgId: GRAFANA_ORG_ID,
    from: timeRange.from,
    to: timeRange.to,
    timezone: 'browser',
    'var-cluster': clusterNameOnCloud,
    'var-node': '$__all',
    'var-gpu': '$__all',
    theme: 'light',
    panelId: panelId,
  });
  return `${grafanaUrl}/d-solo/${GRAFANA_DASHBOARD_SLUG}?${params.toString()}&__feature.dashboardSceneSolo`;
};

/**
 * Reusable GPU Metrics Section component
 *
 * @param {Object} props
 * @param {string} props.clusterNameOnCloud - The cluster name for filtering metrics
 * @param {string} props.displayName - The name to show in the "Showing:" text
 * @param {number} props.refreshTrigger - Increment to trigger iframe refresh
 * @param {string} props.storageKey - LocalStorage key for expanded state
 * @param {React.ReactNode} props.headerExtra - Optional extra content for header (e.g., task selector)
 * @param {string} props.noMetricsMessage - Custom message when no metrics available
 * @param {Date|number|null} props.startTime - Workload start time (for lifetime range)
 * @param {Date|number|null} props.endTime - Workload end time (null if still running)
 */
export function GPUMetricsSection({
  clusterNameOnCloud,
  displayName,
  refreshTrigger = 0,
  storageKey = 'skypilot-gpu-metrics-expanded',
  headerExtra = null,
  noMetricsMessage = 'No GPU metrics available.',
  startTime = null,
  endTime = null,
}) {
  const startMs = useMemo(() => toEpochMs(startTime), [startTime]);
  const endMs = useMemo(() => toEpochMs(endTime), [endTime]);
  const isTerminated = endMs != null;

  const [timeRange, setTimeRange] = useState(() => {
    if (startMs && endMs) {
      // Terminated workload: default to lifetime
      return { from: String(startMs), to: String(endMs) };
    }
    return { from: 'now-1h', to: 'now' };
  });

  // Reset time range when startMs/endMs change (e.g., switching tasks
  // in multi-task job groups). useState lazy initializer only runs once,
  // so we need this effect to keep the time range in sync with props.
  useEffect(() => {
    if (startMs && endMs) {
      setTimeRange({ from: String(startMs), to: String(endMs) });
    } else if (startMs) {
      setTimeRange({ from: String(startMs), to: 'now' });
    } else {
      setTimeRange({ from: 'now-1h', to: 'now' });
    }
  }, [startMs, endMs]);

  const [isExpanded, setIsExpanded] = useState(() => {
    if (typeof window !== 'undefined') {
      const saved = localStorage.getItem(storageKey);
      return saved === 'true';
    }
    return false;
  });

  const handleTimeRangePreset = (preset) => {
    if (isTerminated && endMs) {
      // For terminated workloads, presets are relative to end time
      const durationMs = parseDurationMs(preset);
      setTimeRange({
        from: String(endMs - durationMs),
        to: String(endMs),
      });
    } else {
      setTimeRange({
        from: `now-${preset}`,
        to: 'now',
      });
    }
  };

  const handleLifetimePreset = () => {
    if (startMs && endMs) {
      setTimeRange({ from: String(startMs), to: String(endMs) });
    } else if (startMs) {
      setTimeRange({ from: String(startMs), to: 'now' });
    }
  };

  const isLifetimeActive = useMemo(() => {
    if (!startMs) return false;
    const expectedTo = endMs ? String(endMs) : 'now';
    return timeRange.from === String(startMs) && timeRange.to === expectedTo;
  }, [timeRange, startMs, endMs]);

  const isPresetActive = (preset) => {
    if (isTerminated && endMs) {
      const durationMs = parseDurationMs(preset);
      return (
        timeRange.from === String(endMs - durationMs) &&
        timeRange.to === String(endMs)
      );
    }
    return (
      timeRange.from === `now-${preset}` && timeRange.to === 'now'
    );
  };

  const toggleExpanded = () => {
    const newValue = !isExpanded;
    setIsExpanded(newValue);
    if (typeof window !== 'undefined') {
      localStorage.setItem(storageKey, String(newValue));
    }
  };

  const openInGrafana = () => {
    const queryParams = new URLSearchParams({
      orgId: GRAFANA_ORG_ID,
      from: timeRange.from,
      to: timeRange.to,
      timezone: 'browser',
      'var-cluster': clusterNameOnCloud,
      'var-node': '$__all',
      'var-gpu': '$__all',
    });
    window.open(
      buildGrafanaUrl(`/d/${GRAFANA_DASHBOARD_SLUG}?${queryParams.toString()}`),
      '_blank'
    );
  };

  return (
    <div className="mt-6">
      <div className="rounded-lg border bg-card text-card-foreground shadow-sm">
        <div
          className={`flex items-center justify-between px-4 ${isExpanded ? 'pt-4' : 'py-4'}`}
        >
          <div className="flex items-center">
            <button
              onClick={toggleExpanded}
              className="flex items-center text-left focus:outline-none hover:text-gray-700 transition-colors duration-200"
            >
              {isExpanded ? (
                <ChevronDownIcon className="w-5 h-5 mr-2" />
              ) : (
                <ChevronRightIcon className="w-5 h-5 mr-2" />
              )}
              <h3 className="text-lg font-semibold">GPU Metrics</h3>
            </button>
            {headerExtra}
          </div>
          <Tooltip content="Open in Grafana">
            <button
              onClick={openInGrafana}
              className="p-1.5 rounded-md text-gray-500 hover:text-gray-700 hover:bg-gray-100 transition-colors"
              aria-label="Open in Grafana"
            >
              <ExternalLinkIcon className="w-4 h-4" />
            </button>
          </Tooltip>
        </div>
        {isExpanded && (
          <div className="p-5">
            {/* Filtering Controls */}
            <div className="mb-4 p-4 bg-gray-50 rounded-md border border-gray-200">
              <div className="flex flex-col sm:flex-row gap-4 items-start sm:items-center">
                {/* Time Range Selection */}
                <div className="flex items-center gap-2">
                  <label className="text-sm font-medium text-gray-700 whitespace-nowrap">
                    Time Range:
                  </label>
                  <div className="flex gap-1">
                    {isTerminated && startMs && (
                      <button
                        onClick={handleLifetimePreset}
                        className={`px-2 py-1 text-xs font-medium rounded border transition-colors ${
                          isLifetimeActive
                            ? 'bg-sky-blue text-white border-sky-blue'
                            : 'bg-white text-gray-600 border-gray-300 hover:bg-gray-50'
                        }`}
                      >
                        Lifetime
                      </button>
                    )}
                    {TIME_RANGE_PRESETS.map((preset) => (
                      <button
                        key={preset.value}
                        onClick={() => handleTimeRangePreset(preset.value)}
                        className={`px-2 py-1 text-xs font-medium rounded border transition-colors ${
                          isPresetActive(preset.value)
                            ? 'bg-sky-blue text-white border-sky-blue'
                            : 'bg-white text-gray-600 border-gray-300 hover:bg-gray-50'
                        }`}
                      >
                        {preset.label}
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              {/* Show current selection info */}
              <div className="mt-2 text-xs text-gray-500">
                Showing: {displayName} • Time:{' '}
                {formatTimeDisplay(timeRange.from)} to{' '}
                {formatTimeDisplay(timeRange.to)}
              </div>
            </div>

            {clusterNameOnCloud ? (
              <div className="grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(300px,1fr))]">
                {GPU_PANELS.map((panel) => (
                  <div
                    key={panel.id}
                    className="bg-white rounded-md border border-gray-200 shadow-sm"
                  >
                    <div className="p-2">
                      <iframe
                        src={buildGrafanaMetricsUrl(
                          panel.id,
                          clusterNameOnCloud,
                          timeRange
                        )}
                        width="100%"
                        height="400"
                        frameBorder="0"
                        title={panel.title}
                        className="rounded"
                        key={`${panel.keyPrefix}-${clusterNameOnCloud}-${timeRange.from}-${timeRange.to}-${refreshTrigger}`}
                      />
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="p-4 text-center text-gray-500 bg-gray-50 rounded-md">
                {noMetricsMessage}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default GPUMetricsSection;
