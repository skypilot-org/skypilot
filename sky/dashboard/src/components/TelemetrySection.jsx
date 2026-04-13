import React, { useState } from 'react';
import {
  ChevronDownIcon,
  ChevronRightIcon,
  ExternalLinkIcon,
} from 'lucide-react';
import { CustomTooltip as Tooltip } from '@/components/utils';
import { getGrafanaUrl, buildGrafanaUrl } from '@/utils/grafana';

/**
 * Extract the Kubernetes context name from a SkyPilot infra string.
 *
 * SkyPilot's full_infra is formatted like "Kubernetes (coreweave-dev)" — the
 * value inside the parentheses is the K8s context name used as the
 * `cluster` label by node_exporter scrapes. Returns null if the string isn't
 * a Kubernetes infra or can't be parsed.
 */
export function extractKubernetesContext(fullInfra) {
  if (!fullInfra || typeof fullInfra !== 'string') return null;
  if (!fullInfra.toLowerCase().includes('kubernetes')) return null;
  const m = fullInfra.match(/\(([^)]+)\)/);
  return m ? m[1].trim() : null;
}

// Grafana configuration constants
const GRAFANA_GPU_DASHBOARD_SLUG =
  'skypilot-dcgm-gpu/skypilot-dcgm-gpu-metrics';
const GRAFANA_CLUSTER_DASHBOARD_SLUG =
  'skypilot-dcgm-cluster-dashboard/skypilot-dcgm-kubernetes-cluster-dashboard';
const GRAFANA_ORG_ID = '1';

// Time range presets
const TIME_RANGE_PRESETS = [
  { label: '15m', value: '15m' },
  { label: '1h', value: '1h' },
  { label: '6h', value: '6h' },
  { label: '24h', value: '24h' },
  { label: '7d', value: '7d' },
];

// Telemetry panels configuration. Each panel specifies which Grafana
// dashboard it lives in so we can mix GPU panels (skypilot-dcgm-gpu) with
// host-level CPU/Memory panels (skypilot-dcgm-cluster-dashboard) in one
// section. The `family` field lets consumers hide GPU panels when the
// underlying cluster/job is CPU-only.
const TELEMETRY_PANELS = [
  {
    id: '1',
    title: 'GPU Utilization',
    keyPrefix: 'gpu-util',
    dashboardSlug: GRAFANA_GPU_DASHBOARD_SLUG,
    family: 'gpu',
  },
  {
    id: '2',
    title: 'GPU Memory Utilization',
    keyPrefix: 'gpu-memory',
    dashboardSlug: GRAFANA_GPU_DASHBOARD_SLUG,
    family: 'gpu',
  },
  {
    id: '3',
    title: 'GPU Temperature',
    keyPrefix: 'gpu-temp',
    dashboardSlug: GRAFANA_GPU_DASHBOARD_SLUG,
    family: 'gpu',
  },
  {
    id: '4',
    title: 'GPU Power Usage',
    keyPrefix: 'gpu-power',
    dashboardSlug: GRAFANA_GPU_DASHBOARD_SLUG,
    family: 'gpu',
  },
  {
    id: '22',
    title: 'CPU Utilization',
    keyPrefix: 'cpu-util',
    dashboardSlug: GRAFANA_CLUSTER_DASHBOARD_SLUG,
    family: 'host',
  },
  {
    id: '21',
    title: 'Memory Utilization',
    keyPrefix: 'mem-util',
    dashboardSlug: GRAFANA_CLUSTER_DASHBOARD_SLUG,
    family: 'host',
  },
];

/**
 * Build Grafana panel URL with filters.
 *
 * GPU panels filter by `var-cluster` set to SkyPilot's `cluster_name_on_cloud`
 * because DCGM is configured to label metrics with that name. Host CPU/Memory
 * panels (from node_exporter) need `var-cluster` set to the Kubernetes context
 * name instead — node_exporter is host-level and has no notion of Sky clusters,
 * so it falls back to the K8s cluster label. When the K8s context isn't known
 * we reuse the SkyPilot cluster name (legacy behavior); the dashboard will
 * render "no data" rather than misleading numbers.
 */
const buildGrafanaPanelUrl = (
  panel,
  clusterNameOnCloud,
  kubernetesContext,
  timeRange
) => {
  const grafanaUrl = getGrafanaUrl();
  const clusterFilter =
    panel.family === 'host' && kubernetesContext
      ? kubernetesContext
      : clusterNameOnCloud;
  const params = new URLSearchParams({
    orgId: GRAFANA_ORG_ID,
    from: timeRange.from,
    to: timeRange.to,
    timezone: 'browser',
    'var-datasource': 'prometheus',
    'var-cluster': clusterFilter,
    'var-node': '$__all',
    'var-host': '$__all',
    'var-gpu': '$__all',
    theme: 'light',
    panelId: panel.id,
  });
  return `${grafanaUrl}/d-solo/${panel.dashboardSlug}?${params.toString()}&__feature.dashboardSceneSolo`;
};

/**
 * Reusable Telemetry section that embeds Grafana panels for GPU metrics
 * (utilization, memory, temperature, power) and host-level CPU/Memory.
 *
 * @param {Object} props
 * @param {string} props.clusterNameOnCloud - The Sky cluster name (used as DCGM `cluster` label for GPU panels)
 * @param {string} props.kubernetesContext - The K8s context name (used as `cluster` label for node_exporter host panels)
 * @param {string} props.displayName - The name to show in the "Showing:" text
 * @param {number} props.refreshTrigger - Increment to trigger iframe refresh
 * @param {string} props.storageKey - LocalStorage key for expanded state
 * @param {React.ReactNode} props.headerExtra - Optional extra content for header (e.g., task selector)
 * @param {string} props.noMetricsMessage - Custom message when no metrics available
 * @param {boolean} props.hasGpu - When false, hide GPU panels and only show CPU/Memory.
 */
export function TelemetrySection({
  clusterNameOnCloud,
  kubernetesContext = null,
  displayName,
  refreshTrigger = 0,
  storageKey = 'skypilot-telemetry-expanded',
  headerExtra = null,
  noMetricsMessage = 'No telemetry available.',
  hasGpu = true,
}) {
  const visiblePanels = hasGpu
    ? TELEMETRY_PANELS
    : TELEMETRY_PANELS.filter((p) => p.family !== 'gpu');
  const [timeRange, setTimeRange] = useState({ from: 'now-1h', to: 'now' });
  const [isExpanded, setIsExpanded] = useState(() => {
    if (typeof window !== 'undefined') {
      const saved = localStorage.getItem(storageKey);
      return saved === 'true';
    }
    return false;
  });

  const handleTimeRangePreset = (preset) => {
    setTimeRange({
      from: `now-${preset}`,
      to: 'now',
    });
  };

  const toggleExpanded = () => {
    const newValue = !isExpanded;
    setIsExpanded(newValue);
    if (typeof window !== 'undefined') {
      localStorage.setItem(storageKey, String(newValue));
    }
  };

  // Open the cluster dashboard (which contains both GPU and host-level
  // panels) so the user lands on the most comprehensive view.
  const openInGrafana = () => {
    const queryParams = new URLSearchParams({
      orgId: GRAFANA_ORG_ID,
      from: timeRange.from,
      to: timeRange.to,
      timezone: 'browser',
      'var-datasource': 'prometheus',
      'var-cluster': clusterNameOnCloud,
      'var-host': '$__all',
      'var-gpu': '$__all',
    });
    window.open(
      buildGrafanaUrl(
        `/d/${GRAFANA_CLUSTER_DASHBOARD_SLUG}?${queryParams.toString()}`
      ),
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
              <h3 className="text-lg font-semibold">Telemetry</h3>
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
                    {TIME_RANGE_PRESETS.map((preset) => (
                      <button
                        key={preset.value}
                        onClick={() => handleTimeRangePreset(preset.value)}
                        className={`px-2 py-1 text-xs font-medium rounded border transition-colors ${
                          timeRange.from === `now-${preset.value}` &&
                          timeRange.to === 'now'
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
                Showing: {displayName} • Time: {timeRange.from} to{' '}
                {timeRange.to}
              </div>
            </div>

            {clusterNameOnCloud ? (
              <div className="grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(300px,1fr))]">
                {visiblePanels.map((panel) => (
                  <div
                    key={panel.id}
                    className="bg-white rounded-md border border-gray-200 shadow-sm"
                  >
                    <div className="p-2">
                      <iframe
                        src={buildGrafanaPanelUrl(
                          panel,
                          clusterNameOnCloud,
                          kubernetesContext,
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

export default TelemetrySection;
