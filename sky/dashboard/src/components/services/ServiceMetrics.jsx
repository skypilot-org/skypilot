'use client';

import React, { useState } from 'react';
import { ExternalLinkIcon } from 'lucide-react';
import { CustomTooltip as Tooltip } from '@/components/utils';
import { getGrafanaUrl, buildGrafanaUrl } from '@/utils/grafana';

// Grafana configuration constants
const GRAFANA_DASHBOARD_SLUG =
  'skypilot-vllm-serving/skypilot-vllm-serving-metrics';
const GRAFANA_ORG_ID = '1';

// Time range presets for serving metrics
const TIME_RANGE_PRESETS = [
  { label: '15m', value: '15m' },
  { label: '1h', value: '1h' },
  { label: '6h', value: '6h' },
  { label: '24h', value: '24h' },
  { label: '7d', value: '7d' },
];

// vLLM serving panels configuration — IDs match official vLLM Grafana dashboard
// Source: https://github.com/vllm-project/vllm/blob/main/examples/online_serving/prometheus_grafana/grafana.json
const VLLM_PANELS = [
  { id: '9', title: 'E2E Request Latency', keyPrefix: 'e2e-latency' },
  { id: '8', title: 'Token Throughput', keyPrefix: 'throughput' },
  { id: '5', title: 'Time to First Token', keyPrefix: 'ttft' },
  { id: '3', title: 'Scheduler State', keyPrefix: 'scheduler' },
  { id: '4', title: 'Cache Utilization', keyPrefix: 'kv-cache' },
  { id: '10', title: 'Inter Token Latency', keyPrefix: 'itl' },
  { id: '14', title: 'Queue Time', keyPrefix: 'queue-time' },
  { id: '11', title: 'Finish Reason', keyPrefix: 'finish-reason' },
];

/**
 * Build Grafana panel URL with service filter
 */
const buildGrafanaMetricsUrl = (panelId, timeRange) => {
  const grafanaUrl = getGrafanaUrl();
  const params = new URLSearchParams({
    orgId: GRAFANA_ORG_ID,
    from: timeRange.from,
    to: timeRange.to,
    timezone: 'browser',
    theme: 'light',
    panelId: panelId,
  });
  return `${grafanaUrl}/d-solo/${GRAFANA_DASHBOARD_SLUG}?${params.toString()}`;
};

/**
 * Service Metrics component that embeds Grafana panels for vLLM serving metrics.
 *
 * When Grafana is available, displays iframe-embedded solo panels for KV cache,
 * request queue, throughput, token generation, and latency metrics.
 * When Grafana is not available, shows a placeholder card.
 *
 * @param {Object} props
 * @param {Object} props.serviceData - Service data with name and endpoint
 * @param {boolean} props.isGrafanaAvailable - Whether Grafana is reachable
 * @param {number} props.refreshTrigger - Increment to trigger iframe refresh
 */
export function ServiceMetrics({
  serviceData,
  isGrafanaAvailable,
  refreshTrigger = 0,
}) {
  const [timeRange, setTimeRange] = useState({ from: 'now-1h', to: 'now' });

  const handleTimeRangePreset = (preset) => {
    setTimeRange({
      from: `now-${preset}`,
      to: 'now',
    });
  };

  const openInGrafana = () => {
    const queryParams = new URLSearchParams({
      orgId: GRAFANA_ORG_ID,
      from: timeRange.from,
      to: timeRange.to,
      timezone: 'browser',
    });
    window.open(
      buildGrafanaUrl(
        `/d/${GRAFANA_DASHBOARD_SLUG}?${queryParams.toString()}`
      ),
      '_blank'
    );
  };

  if (!isGrafanaAvailable) {
    return (
      <div className="mt-6">
        <div className="rounded-lg border bg-card text-card-foreground shadow-sm">
          <div className="p-6">
            <h3 className="text-lg font-semibold mb-2">Serving Metrics</h3>
            <p className="text-sm text-gray-500">
              Grafana not available. Deploy Grafana alongside the SkyPilot API
              server to view serving metrics. Service metrics include KV cache
              usage, request queue depth, throughput, and latency.
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="mt-6">
      <div className="rounded-lg border bg-card text-card-foreground shadow-sm">
        <div className="flex items-center justify-between px-4 pt-4">
          <h3 className="text-lg font-semibold">Serving Metrics</h3>
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
              Showing: {serviceData.name} &bull; Time: {timeRange.from} to{' '}
              {timeRange.to}
            </div>
          </div>

          {/* Grafana Panels Grid */}
          <div className="grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(300px,1fr))]">
            {VLLM_PANELS.map((panel) => (
              <div
                key={panel.id}
                className="bg-white rounded-md border border-gray-200 shadow-sm"
              >
                <div className="p-2">
                  <iframe
                    src={buildGrafanaMetricsUrl(
                      panel.id,
                      timeRange
                    )}
                    width="100%"
                    height="400"
                    frameBorder="0"
                    title={panel.title}
                    className="rounded"
                    key={`${panel.keyPrefix}-${serviceData.name}-${timeRange.from}-${timeRange.to}-${refreshTrigger}`}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

export default ServiceMetrics;
