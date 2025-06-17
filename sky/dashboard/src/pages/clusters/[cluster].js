import React, { useState, useEffect, useCallback } from 'react';
import { CircularProgress } from '@mui/material';
import { ClusterJobs } from '@/components/jobs';
import { useRouter } from 'next/router';
import { Layout } from '@/components/elements/layout';
import Link from 'next/link';
import { Status2Actions } from '@/components/clusters';
import { StatusBadge } from '@/components/elements/StatusBadge';
import { Card, CardHeader, CardContent } from '@/components/ui/card';
import { useClusterDetails } from '@/data/connectors/clusters';
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
} from '@/components/utils';
import { checkGrafanaAvailability, getGrafanaUrl } from '@/utils/grafana';
import {
  SSHInstructionsModal,
  VSCodeInstructionsModal,
} from '@/components/elements/modals';
import { useMobile } from '@/hooks/useMobile';
import Head from 'next/head';

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

    return `${grafanaUrl}/d-solo/skypilot-dcgm-gpu/skypilot-dcgm-gpu-metrics?orgId=1&from=${encodeURIComponent(timeRange.from)}&to=${encodeURIComponent(timeRange.to)}&timezone=browser&var-cluster=${encodeURIComponent(clusterParam)}&var-instance=$__all&var-gpu=$__all&theme=light&panelId=${panelId}&__feature.dashboardSceneSolo`;
  };

  // Update isInitialLoad when cluster details are first loaded (not waiting for jobs)
  React.useEffect(() => {
    if (!clusterDetailsLoading && isInitialLoad) {
      setIsInitialLoad(false);
    }
  }, [clusterDetailsLoading, isInitialLoad]);

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

        {clusterDetailsLoading && isInitialLoad ? (
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
          />
        ) : null}

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
}) {
  const [isYamlExpanded, setIsYamlExpanded] = useState(false);
  const [isCopied, setIsCopied] = useState(false);
  const [isCommandCopied, setIsCommandCopied] = useState(false);

  const toggleYamlExpanded = () => {
    setIsYamlExpanded(!isYamlExpanded);
  };

  const copyYamlToClipboard = async () => {
    try {
      const formattedYaml = formatYaml(clusterData.task_yaml);
      await navigator.clipboard.writeText(formattedYaml);
      setIsCopied(true);
      setTimeout(() => setIsCopied(false), 2000); // Reset after 2 seconds
    } catch (err) {
      console.error('Failed to copy YAML to clipboard:', err);
    }
  };

  const copyCommandToClipboard = async () => {
    try {
      await navigator.clipboard.writeText(clusterData.command);
      setIsCommandCopied(true);
      setTimeout(() => setIsCommandCopied(false), 2000); // Reset after 2 seconds
    } catch (err) {
      console.error('Failed to copy command to clipboard:', err);
    }
  };

  const formatYaml = (yamlString) => {
    if (!yamlString) return 'No YAML available';

    try {
      // Parse the YAML string into an object
      const parsed = yaml.load(yamlString);

      // Re-serialize with pipe syntax for multiline strings
      const formatted = yaml.dump(parsed, {
        lineWidth: -1, // Disable line wrapping
        styles: {
          '!!str': 'literal', // Use pipe (|) syntax for multiline strings
        },
        quotingType: "'", // Use single quotes for strings that need quoting
        forceQuotes: false, // Only quote when necessary
        noRefs: true, // Disable YAML references
        sortKeys: false, // Preserve original key order
        condenseFlow: false, // Don't condense flow style
        indent: 2, // Use 2 spaces for indentation
      });

      // Add blank lines between top-level sections for better readability
      const lines = formatted.split('\n');
      const result = [];
      let prevIndent = -1;

      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        const currentIndent = line.search(/\S/); // Find first non-whitespace

        // Add blank line before new top-level sections (indent = 0)
        if (currentIndent === 0 && prevIndent >= 0 && i > 0) {
          result.push('');
        }

        result.push(line);
        prevIndent = currentIndent;
      }

      return result.join('\n').trim();
    } catch (e) {
      console.error('YAML formatting error:', e);
      // If parsing fails, return the original string
      return yamlString;
    }
  };

  const hasCreationArtifacts = clusterData?.command || clusterData?.task_yaml;

  return (
    <div>
      {/* Cluster Info Card */}
      <div className="mb-6">
        <div className="rounded-lg border bg-card text-card-foreground shadow-sm">
          <div className="p-5">
            <h3 className="text-lg font-semibold mb-4">Details</h3>
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
                <div className="text-base mt-1">{clusterData.cluster}</div>
              </div>
              <div>
                <div className="text-gray-600 font-medium text-base">User</div>
                <div className="text-base mt-1">{clusterData.user}</div>
              </div>
              <div>
                <div className="text-gray-600 font-medium text-base">Infra</div>
                <div className="text-base mt-1">
                  {clusterData.infra ? (
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
                    ? new Date(clusterData.time).toLocaleString()
                    : 'N/A'}
                </div>
              </div>
              <div>
                <div className="text-gray-600 font-medium text-base">
                  Autostop
                </div>
                <div className="text-base mt-1">
                  {formatAutostop(clusterData.autostop, clusterData.to_down)}
                </div>
              </div>

              {/* Created by section - spans both columns */}
              {hasCreationArtifacts && (
                <div className="col-span-2">
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

                  <div className="space-y-4 mt-3">
                    {/* Creation Command */}
                    {clusterData.command && (
                      <div>
                        <div className="bg-gray-50 border border-gray-200 rounded-md p-3">
                          <code className="text-sm text-gray-800 font-mono break-all">
                            {clusterData.command}
                          </code>
                        </div>
                      </div>
                    )}

                    {/* Task YAML - Collapsible */}
                    {clusterData.task_yaml &&
                      clusterData.task_yaml !== '{}' &&
                      !clusterData.cluster.startsWith('sky-jobs-controller-') &&
                      !clusterData.cluster.startsWith(
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
                              <pre className="text-sm text-gray-800 font-mono whitespace-pre-wrap">
                                {formatYaml(clusterData.task_yaml)}
                              </pre>
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

      {/* Jobs Table */}
      <div className="mb-6">
        <ClusterJobs
          clusterName={clusterData?.cluster}
          clusterJobData={clusterJobData}
          loading={clusterJobsLoading}
          refreshClusterJobsOnly={refreshClusterJobsOnly}
        />
      </div>

      {/* GPU Metrics Section - Only show for Kubernetes in-cluster */}
      {clusterData &&
        clusterData.full_infra === 'Kubernetes (in-cluster)' &&
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
                    {matchedClusterName &&
                      matchedClusterName !== clusterData?.cluster && (
                        <span> • Matched: {matchedClusterName}</span>
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
    </div>
  );
}

export default ClusterDetails;
