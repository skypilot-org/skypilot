import React, { useState, useEffect, useCallback } from 'react';
import { CircularProgress } from '@mui/material';
import { ClusterJobs } from '@/components/jobs';
import { useRouter } from 'next/router';
import { Layout } from '@/components/elements/layout';
import Link from 'next/link';
import { Status2Actions } from '@/components/clusters';
import { StatusBadge } from '@/components/elements/StatusBadge';
import { Card } from '@/components/ui/card';
import {
  useClusterDetails,
  getClusterHistory,
} from '@/data/connectors/clusters';
import dashboardCache from '@/lib/cache';
import {
  RotateCwIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  CopyIcon,
  CheckIcon,
} from 'lucide-react';
import yaml from 'js-yaml';
import {
  CustomTooltip as Tooltip,
  NonCapitalizedTooltip,
  formatFullTimestamp,
} from '@/components/utils';
import { checkGrafanaAvailability, getGrafanaUrl } from '@/utils/grafana';
import {
  SSHInstructionsModal,
  VSCodeInstructionsModal,
} from '@/components/elements/modals';
import { useMobile } from '@/hooks/useMobile';
import Head from 'next/head';
import { formatYaml } from '@/lib/yamlUtils';
import { UserDisplay } from '@/components/elements/UserDisplay';
import { YamlHighlighter } from '@/components/YamlHighlighter';

// Helper function to format autostop information, similar to _get_autostop in CLI utils
const formatAutostop = (autostop, toDown) => {
  let autostopStr = '';
  let separation = '';

  if (autostop >= 0) {
    autostopStr = autostop + 'm';
    separation = ' ';
  }

  if (toDown) {
    autostopStr += `${separation}(down)`;
  }

  if (autostopStr === '') {
    autostopStr = '-';
  }

  return autostopStr;
};

function ClusterDetails() {
  const router = useRouter();
  const { cluster } = router.query; // Access the dynamic part of the URL

  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isInitialLoad, setIsInitialLoad] = useState(true);
  const [isSSHModalOpen, setIsSSHModalOpen] = useState(false);
  const [isVSCodeModalOpen, setIsVSCodeModalOpen] = useState(false);
  const [historyData, setHistoryData] = useState(null);
  const [isHistoricalCluster, setIsHistoricalCluster] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const isMobile = useMobile();
  const [timeRange, setTimeRange] = useState({
    from: 'now-1h',
    to: 'now',
  });
  const {
    clusterData,
    clusterJobData,
    loading,
    clusterDetailsLoading,
    clusterJobsLoading,
    refreshData,
    refreshClusterJobsOnly,
  } = useClusterDetails({ cluster });

  // GPU metrics state
  const [matchedClusterName, setMatchedClusterName] = useState(null);
  const [isLoadingClusterMatch, setIsLoadingClusterMatch] = useState(false);
  const [isGrafanaAvailable, setIsGrafanaAvailable] = useState(false);

  // Check Grafana availability on mount
  useEffect(() => {
    const checkGrafana = async () => {
      const available = await checkGrafanaAvailability();
      setIsGrafanaAvailable(available);
    };

    if (typeof window !== 'undefined') {
      checkGrafana();
    }
  }, []);

  // Fetch available clusters from Grafana and find matching cluster
  const fetchMatchingCluster = useCallback(async () => {
    if (!isGrafanaAvailable || !clusterData?.cluster) return;

    setIsLoadingClusterMatch(true);
    try {
      const grafanaUrl = getGrafanaUrl();
      const endpoint =
        '/api/datasources/proxy/1/api/v1/label/label_skypilot_cluster/values';

      const response = await fetch(`${grafanaUrl}${endpoint}`, {
        method: 'GET',
        credentials: 'include',
        headers: {
          Accept: 'application/json',
        },
      });

      if (response.ok) {
        const data = await response.json();
        if (data.data && data.data.length > 0) {
          // Find cluster that matches our current cluster name as prefix
          const matchingCluster = data.data.find((cluster) =>
            cluster.startsWith(clusterData.cluster)
          );
          if (matchingCluster) {
            setMatchedClusterName(matchingCluster);
          }
        }
      }
    } catch (error) {
      console.error('Error fetching matching cluster:', error);
    } finally {
      setIsLoadingClusterMatch(false);
    }
  }, [clusterData?.cluster, isGrafanaAvailable]);

  // Fetch matching cluster when component mounts and Grafana is available
  useEffect(() => {
    if (isGrafanaAvailable && clusterData?.cluster) {
      fetchMatchingCluster();
    }
  }, [clusterData?.cluster, fetchMatchingCluster, isGrafanaAvailable]);

  // Function to build Grafana panel URL with filters
  const buildGrafanaMetricsUrl = (panelId) => {
    const grafanaUrl = getGrafanaUrl();
    // Use the matched cluster name if available, otherwise fall back to the display name
    const clusterParam = matchedClusterName || clusterData?.cluster || '$__all';

    return `${grafanaUrl}/d-solo/skypilot-dcgm-gpu/skypilot-dcgm-gpu-metrics?orgId=1&from=${encodeURIComponent(timeRange.from)}&to=${encodeURIComponent(timeRange.to)}&timezone=browser&var-cluster=${encodeURIComponent(clusterParam)}&var-node=$__all&var-gpu=$__all&theme=light&panelId=${panelId}&__feature.dashboardSceneSolo`;
  };

  // Update isInitialLoad when cluster details are first loaded (not waiting for jobs)
  React.useEffect(() => {
    if (!clusterDetailsLoading && isInitialLoad) {
      setIsInitialLoad(false);
    }
  }, [clusterDetailsLoading, isInitialLoad]);

  // Check for historical cluster if active cluster is not found
  React.useEffect(() => {
    const checkHistoryCluster = async () => {
      if (!cluster || clusterDetailsLoading || clusterData) return;

      setHistoryLoading(true);
      try {
        const historyData = await dashboardCache.get(getClusterHistory);
        const foundHistoryCluster = historyData.find(
          (c) => c.cluster_hash === cluster || c.cluster === cluster
        );
        if (foundHistoryCluster) {
          setHistoryData(foundHistoryCluster);
          setIsHistoricalCluster(true);
        }
      } catch (error) {
        console.error('Error fetching cluster history:', error);
      } finally {
        setHistoryLoading(false);
      }
    };

    // Only check history if we've finished loading and no active cluster found
    if (!clusterDetailsLoading && !clusterData) {
      checkHistoryCluster();
    }
  }, [cluster, clusterDetailsLoading, clusterData]);

  const handleManualRefresh = async () => {
    setIsRefreshing(true);
    await refreshData();
    setIsRefreshing(false);
  };

  const handleConnectClick = () => {
    setIsSSHModalOpen(true);
  };

  const handleVSCodeClick = () => {
    setIsVSCodeModalOpen(true);
  };

  const handleTimeRangePreset = (preset) => {
    setTimeRange({
      from: `now-${preset}`,
      to: 'now',
    });
  };

  // Render loading state until data is available
  if (!router.isReady) {
    return <div>Loading...</div>;
  }

  const title = cluster
    ? `Cluster: ${cluster} | SkyPilot Dashboard`
    : 'Cluster Details | SkyPilot Dashboard';

  return (
    <>
      <Head>
        <title>{title}</title>
      </Head>
      <>
        <div className="flex items-center justify-between mb-4 h-5">
          <div className="text-base flex items-center">
            <Link href="/clusters" className="text-sky-blue hover:underline">
              Sky Clusters
            </Link>
            <span className="mx-2 text-gray-500">›</span>
            <Link
              href={`/clusters/${cluster}`}
              className="text-sky-blue hover:underline"
            >
              {cluster}
            </Link>
          </div>

          <div className="text-sm flex items-center">
            <div className="text-sm flex items-center">
              {(clusterDetailsLoading || isRefreshing) && (
                <div className="flex items-center mr-4">
                  <CircularProgress size={15} className="mt-0" />
                  <span className="ml-2 text-gray-500">Loading...</span>
                </div>
              )}
              {clusterData && (
                <div className="flex items-center space-x-4">
                  <Tooltip
                    content="Refresh"
                    className="text-sm text-muted-foreground"
                  >
                    <button
                      onClick={handleManualRefresh}
                      disabled={clusterDetailsLoading || isRefreshing}
                      className="text-sky-blue hover:text-sky-blue-bright font-medium inline-flex items-center"
                    >
                      <RotateCwIcon className="w-4 h-4 mr-1.5" />
                      {!isMobile && <span>Refresh</span>}
                    </button>
                  </Tooltip>
                  <Status2Actions
                    withLabel={true}
                    cluster={clusterData.cluster}
                    status={clusterData.status}
                    onOpenSSHModal={handleConnectClick}
                    onOpenVSCodeModal={handleVSCodeClick}
                  />
                </div>
              )}
            </div>
          </div>
        </div>

        {(clusterDetailsLoading && isInitialLoad) || historyLoading ? (
          <div className="flex justify-center items-center py-12">
            <CircularProgress size={24} className="mr-2" />
            <span className="text-gray-500">Loading cluster details...</span>
          </div>
        ) : clusterData ? (
          <ActiveTab
            clusterData={clusterData}
            clusterJobData={clusterJobData}
            clusterJobsLoading={clusterJobsLoading}
            refreshClusterJobsOnly={refreshClusterJobsOnly}
            isVSCodeModalOpen={isVSCodeModalOpen}
            setIsVSCodeModalOpen={setIsVSCodeModalOpen}
            timeRange={timeRange}
            handleTimeRangePreset={handleTimeRangePreset}
            buildGrafanaMetricsUrl={buildGrafanaMetricsUrl}
            matchedClusterName={matchedClusterName}
            isLoadingClusterMatch={isLoadingClusterMatch}
            isGrafanaAvailable={isGrafanaAvailable}
            isHistoricalCluster={false}
          />
        ) : isHistoricalCluster && historyData ? (
          <ActiveTab
            clusterData={historyData}
            clusterJobData={[]}
            clusterJobsLoading={false}
            refreshClusterJobsOnly={() => {}}
            isVSCodeModalOpen={false}
            setIsVSCodeModalOpen={() => {}}
            timeRange={timeRange}
            handleTimeRangePreset={handleTimeRangePreset}
            buildGrafanaMetricsUrl={buildGrafanaMetricsUrl}
            matchedClusterName={null}
            isLoadingClusterMatch={false}
            isGrafanaAvailable={false}
            isHistoricalCluster={true}
          />
        ) : (
          <div className="flex justify-center items-center py-12">
            <span className="text-gray-500">
              Cluster not found in active clusters or history.
            </span>
          </div>
        )}

        {/* SSH Instructions Modal */}
        <SSHInstructionsModal
          isOpen={isSSHModalOpen}
          onClose={() => setIsSSHModalOpen(false)}
          cluster={cluster}
        />

        {/* VSCode Instructions Modal */}
        <VSCodeInstructionsModal
          isOpen={isVSCodeModalOpen}
          onClose={() => setIsVSCodeModalOpen(false)}
          cluster={cluster}
        />
      </>
    </>
  );
}

function ActiveTab({
  clusterData,
  clusterJobData,
  clusterJobsLoading,
  refreshClusterJobsOnly,
  isVSCodeModalOpen,
  setIsVSCodeModalOpen,
  timeRange,
  handleTimeRangePreset,
  buildGrafanaMetricsUrl,
  matchedClusterName,
  isLoadingClusterMatch,
  isGrafanaAvailable,
  isHistoricalCluster = false,
}) {
  const [isYamlExpanded, setIsYamlExpanded] = useState(false);
  const [isCopied, setIsCopied] = useState(false);
  const [isCommandCopied, setIsCommandCopied] = useState(false);

  const toggleYamlExpanded = () => {
    setIsYamlExpanded(!isYamlExpanded);
  };

  const copyYamlToClipboard = async () => {
    try {
      const yamlContent =
        clusterData.task_yaml || clusterData.last_creation_yaml;
      const formattedYaml = formatYaml(yamlContent);
      await navigator.clipboard.writeText(formattedYaml);
      setIsCopied(true);
      setTimeout(() => setIsCopied(false), 2000); // Reset after 2 seconds
    } catch (err) {
      console.error('Failed to copy YAML to clipboard:', err);
    }
  };

  const copyCommandToClipboard = async () => {
    try {
      const command = clusterData.command || clusterData.last_creation_command;
      await navigator.clipboard.writeText(command);
      setIsCommandCopied(true);
      setTimeout(() => setIsCommandCopied(false), 2000); // Reset after 2 seconds
    } catch (err) {
      console.error('Failed to copy command to clipboard:', err);
    }
  };

  const hasCreationArtifacts =
    clusterData?.last_creation_command ||
    clusterData?.last_creation_yaml ||
    clusterData?.command ||
    clusterData?.task_yaml;

  // Helper functions for historical clusters
  const formatDuration = (durationSeconds) => {
    if (!durationSeconds || durationSeconds === 0) {
      return '-';
    }

    // Convert to a whole number if it's a float
    durationSeconds = Math.floor(durationSeconds);

    const units = [
      { value: 31536000, label: 'y' }, // years (365 days)
      { value: 2592000, label: 'mo' }, // months (30 days)
      { value: 86400, label: 'd' }, // days
      { value: 3600, label: 'h' }, // hours
      { value: 60, label: 'm' }, // minutes
      { value: 1, label: 's' }, // seconds
    ];

    let remaining = durationSeconds;
    let result = '';
    let count = 0;

    for (const unit of units) {
      if (remaining >= unit.value && count < 2) {
        const value = Math.floor(remaining / unit.value);
        result += `${value}${unit.label} `;
        remaining %= unit.value;
        count++;
      }
    }

    return result.trim() || '0s';
  };

  const formatCost = (cost) => {
    if (cost === null || cost === undefined || cost === 0) {
      return '-';
    }
    // Convert to number and check if it's valid
    const numericCost = Number(cost);
    if (isNaN(numericCost)) {
      return '-';
    }
    return `$${numericCost.toFixed(2)}`;
  };

  return (
    <div>
      {/* Cluster Info Card */}
      <div className="mb-6">
        <div className="rounded-lg border bg-card text-card-foreground shadow-sm">
          <div className="flex items-center justify-between px-4 pt-4">
            <h3 className="text-lg font-semibold">
              {isHistoricalCluster ? 'Historical Cluster Details' : 'Details'}
            </h3>
          </div>
          <div className="p-4">
            <div className="grid grid-cols-2 gap-6">
              <div>
                <div className="text-gray-600 font-medium text-base">
                  Status
                </div>
                <div className="text-base mt-1">
                  <StatusBadge status={clusterData.status} />
                </div>
              </div>
              <div>
                <div className="text-gray-600 font-medium text-base">
                  Cluster
                </div>
                <div className="text-base mt-1">
                  {clusterData.cluster || clusterData.name}
                </div>
              </div>
              <div>
                <div className="text-gray-600 font-medium text-base">User</div>
                <div className="text-base mt-1">
                  <UserDisplay
                    username={clusterData.user}
                    userHash={clusterData.user_hash}
                  />
                </div>
              </div>
              <div>
                <div className="text-gray-600 font-medium text-base">
                  {isHistoricalCluster ? 'Cloud' : 'Infra'}
                </div>
                <div className="text-base mt-1">
                  {isHistoricalCluster ? (
                    clusterData.cloud || 'N/A'
                  ) : clusterData.infra ? (
                    <NonCapitalizedTooltip
                      content={clusterData.full_infra || clusterData.infra}
                      className="text-sm text-muted-foreground"
                    >
                      <span>
                        <Link
                          href="/infra"
                          className="text-blue-600 hover:underline"
                        >
                          {clusterData.cloud ||
                            clusterData.infra.split('(')[0].trim()}
                        </Link>
                        {clusterData.infra.includes('(') && (
                          <span>
                            {' ' +
                              clusterData.infra.substring(
                                clusterData.infra.indexOf('(')
                              )}
                          </span>
                        )}
                      </span>
                    </NonCapitalizedTooltip>
                  ) : (
                    'N/A'
                  )}
                </div>
              </div>
              <div>
                <div className="text-gray-600 font-medium text-base">
                  Resources
                </div>
                <div className="text-base mt-1">
                  {clusterData.resources_str_full ||
                    clusterData.resources_str ||
                    'N/A'}
                </div>
              </div>
              <div>
                <div className="text-gray-600 font-medium text-base">
                  Started
                </div>
                <div className="text-base mt-1">
                  {clusterData.time
                    ? formatFullTimestamp(new Date(clusterData.time))
                    : 'N/A'}
                </div>
              </div>
              <div>
                <div className="text-gray-600 font-medium text-base">
                  Last Event
                </div>
                <div className="text-base mt-1">
                  <NonCapitalizedTooltip
                    content={clusterData.last_event || '-'}
                    className="text-sm text-muted-foreground"
                  >
                    <span>{clusterData.last_event || '-'}</span>
                  </NonCapitalizedTooltip>
                </div>
              </div>
              {/* Show duration and cost for historical clusters */}
              {isHistoricalCluster ? (
                <>
                  <div>
                    <div className="text-gray-600 font-medium text-base">
                      Duration
                    </div>
                    <div className="text-base mt-1">
                      {formatDuration(clusterData.duration)}
                    </div>
                  </div>
                  <div>
                    <div className="text-gray-600 font-medium text-base">
                      Cost
                    </div>
                    <div className="text-base mt-1">
                      {formatCost(clusterData.total_cost)}
                    </div>
                  </div>
                </>
              ) : (
                <div>
                  <div className="text-gray-600 font-medium text-base">
                    Autostop
                  </div>
                  <div className="text-base mt-1">
                    {formatAutostop(clusterData.autostop, clusterData.to_down)}
                  </div>
                </div>
              )}

              {/* Created by section - spans both columns */}
              {hasCreationArtifacts && (
                <div className="col-span-2">
                  {(clusterData.command ||
                    clusterData.last_creation_command) && (
                    <div className="flex items-center">
                      <div className="text-gray-600 font-medium text-base">
                        Entrypoint
                      </div>
                      {clusterData.command && (
                        <Tooltip
                          content={isCommandCopied ? 'Copied!' : 'Copy command'}
                          className="text-muted-foreground"
                        >
                          <button
                            onClick={copyCommandToClipboard}
                            className="flex items-center text-gray-500 hover:text-gray-700 transition-colors duration-200 p-1 ml-2"
                          >
                            {isCommandCopied ? (
                              <CheckIcon className="w-4 h-4 text-green-600" />
                            ) : (
                              <CopyIcon className="w-4 h-4" />
                            )}
                          </button>
                        </Tooltip>
                      )}
                    </div>
                  )}

                  <div className="space-y-4 mt-3">
                    {/* Creation Command */}
                    {(clusterData.command ||
                      clusterData.last_creation_command) && (
                      <div>
                        <div className="bg-gray-50 border border-gray-200 rounded-md p-3">
                          <code className="text-sm text-gray-800 font-mono break-all">
                            {clusterData.command ||
                              clusterData.last_creation_command}
                          </code>
                        </div>
                      </div>
                    )}

                    {/* Task YAML - Collapsible */}
                    {(clusterData.task_yaml ||
                      clusterData.last_creation_yaml) &&
                      clusterData.task_yaml !== '{}' &&
                      clusterData.last_creation_yaml !== '{}' &&
                      !(clusterData.cluster || clusterData.name)?.startsWith(
                        'sky-jobs-controller-'
                      ) &&
                      !(clusterData.cluster || clusterData.name)?.startsWith(
                        'sky-serve-controller-'
                      ) && (
                        <div>
                          <div className="flex items-center mb-2">
                            <button
                              onClick={toggleYamlExpanded}
                              className="flex items-center text-left focus:outline-none text-gray-700 hover:text-gray-900 transition-colors duration-200"
                            >
                              {isYamlExpanded ? (
                                <ChevronDownIcon className="w-4 h-4 mr-1" />
                              ) : (
                                <ChevronRightIcon className="w-4 h-4 mr-1" />
                              )}
                              <span className="text-base">
                                Show SkyPilot YAML
                              </span>
                            </button>

                            <Tooltip
                              content={isCopied ? 'Copied!' : 'Copy YAML'}
                              className="text-muted-foreground"
                            >
                              <button
                                onClick={copyYamlToClipboard}
                                className="flex items-center text-gray-500 hover:text-gray-700 transition-colors duration-200 p-1 ml-2"
                              >
                                {isCopied ? (
                                  <CheckIcon className="w-4 h-4 text-green-600" />
                                ) : (
                                  <CopyIcon className="w-4 h-4" />
                                )}
                              </button>
                            </Tooltip>
                          </div>

                          {isYamlExpanded && (
                            <div className="bg-gray-50 border border-gray-200 rounded-md p-3 max-h-96 overflow-y-auto">
                              <YamlHighlighter className="whitespace-pre-wrap">
                                {formatYaml(
                                  clusterData.task_yaml ||
                                    clusterData.last_creation_yaml
                                )}
                              </YamlHighlighter>
                            </div>
                          )}
                        </div>
                      )}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* GPU Metrics Section - Show for all Kubernetes clusters (in-cluster and external), but not SSH node pools */}
      {clusterData &&
        clusterData.full_infra &&
        clusterData.full_infra.includes('Kubernetes') &&
        !clusterData.full_infra.includes('SSH') &&
        !clusterData.full_infra.includes('ssh') &&
        isGrafanaAvailable && (
          <div className="mb-6">
            <div className="rounded-lg border bg-card text-card-foreground shadow-sm">
              <div className="p-5">
                <div className="flex items-center justify-between mb-4">
                  <h3 className="text-lg font-semibold">GPU Metrics</h3>
                </div>

                {/* Filtering Controls */}
                <div className="mb-4 p-4 bg-gray-50 rounded-md border border-gray-200">
                  <div className="flex flex-col sm:flex-row gap-4 items-start sm:items-center">
                    {/* Time Range Selection */}
                    <div className="flex items-center gap-2">
                      <label className="text-sm font-medium text-gray-700 whitespace-nowrap">
                        Time Range:
                      </label>
                      <div className="flex gap-1">
                        {[
                          { label: '15m', value: '15m' },
                          { label: '1h', value: '1h' },
                          { label: '6h', value: '6h' },
                          { label: '24h', value: '24h' },
                          { label: '7d', value: '7d' },
                        ].map((preset) => (
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
                    Showing: {clusterData?.cluster} • Time: {timeRange.from} to{' '}
                    {timeRange.to}
                    {isLoadingClusterMatch && (
                      <span> • Finding cluster data...</span>
                    )}
                  </div>
                </div>

                <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
                  {/* GPU Utilization */}
                  <div className="bg-white rounded-md border border-gray-200 shadow-sm">
                    <div className="p-2">
                      <iframe
                        src={buildGrafanaMetricsUrl('1')}
                        width="100%"
                        height="400"
                        frameBorder="0"
                        title="GPU Utilization"
                        className="rounded"
                        key={`gpu-util-${clusterData?.cluster}-${timeRange.from}-${timeRange.to}`}
                      />
                    </div>
                  </div>

                  {/* GPU Memory Utilization */}
                  <div className="bg-white rounded-md border border-gray-200 shadow-sm">
                    <div className="p-2">
                      <iframe
                        src={buildGrafanaMetricsUrl('2')}
                        width="100%"
                        height="400"
                        frameBorder="0"
                        title="GPU Memory Utilization"
                        className="rounded"
                        key={`gpu-memory-${clusterData?.cluster}-${timeRange.from}-${timeRange.to}`}
                      />
                    </div>
                  </div>

                  {/* GPU Power Usage */}
                  <div className="bg-white rounded-md border border-gray-200 shadow-sm">
                    <div className="p-2">
                      <iframe
                        src={buildGrafanaMetricsUrl('4')}
                        width="100%"
                        height="400"
                        frameBorder="0"
                        title="GPU Power Usage"
                        className="rounded"
                        key={`gpu-power-${clusterData?.cluster}-${timeRange.from}-${timeRange.to}`}
                      />
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

      {/* Jobs Table - Only show for active clusters */}
      {!isHistoricalCluster && (
        <div className="mb-8">
          <ClusterJobs
            clusterName={clusterData.cluster}
            clusterJobData={clusterJobData}
            loading={clusterJobsLoading}
            refreshClusterJobsOnly={refreshClusterJobsOnly}
          />
        </div>
      )}
    </div>
  );
}

export default ClusterDetails;
