import React, { useState, useEffect } from 'react';
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
import { checkGrafanaAvailability } from '@/utils/grafana';
import {
  SSHInstructionsModal,
  VSCodeInstructionsModal,
} from '@/components/elements/modals';
import { useMobile } from '@/hooks/useMobile';
import Head from 'next/head';
import { formatYaml } from '@/lib/yamlUtils';
import { UserDisplay } from '@/components/elements/UserDisplay';
import { YamlHighlighter } from '@/components/YamlHighlighter';
import { PluginSlot } from '@/plugins/PluginSlot';
import { GPUMetricsSection } from '@/components/GPUMetricsSection';

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
  // Counter incremented on refresh to force GPU metrics iframes to reload.
  // When this value changes, the iframe key changes, causing React to remount the iframe.
  const [gpuMetricsRefreshTrigger, setGpuMetricsRefreshTrigger] = useState(0);
  const isMobile = useMobile();
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
  const [isGrafanaAvailable, setIsGrafanaAvailable] = useState(false);

  // Check Grafana availability on mount
  useEffect(() => {
    const checkGrafana = async () => {
      const available = await checkGrafanaAvailability();
      setIsGrafanaAvailable(available);
    };
    checkGrafana();
  }, []);

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
        const historyData = await dashboardCache.get(getClusterHistory, [
          cluster,
        ]);
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
    // Increment GPU metrics refresh trigger to force iframe reload
    setGpuMetricsRefreshTrigger((prev) => prev + 1);
    setIsRefreshing(false);
  };

  const handleConnectClick = () => {
    setIsSSHModalOpen(true);
  };

  const handleVSCodeClick = () => {
    setIsVSCodeModalOpen(true);
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
            isGrafanaAvailable={isGrafanaAvailable}
            gpuMetricsRefreshTrigger={gpuMetricsRefreshTrigger}
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
            isGrafanaAvailable={false}
            gpuMetricsRefreshTrigger={0}
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
  isGrafanaAvailable,
  gpuMetricsRefreshTrigger,
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
                  <PluginSlot
                    name="clusters.detail.status.badge"
                    context={clusterData}
                    fallback={<StatusBadge status={clusterData.status} />}
                  />
                </div>
              </div>
              <div>
                <div className="text-gray-600 font-medium text-base">
                  Cluster
                </div>
                <div className="text-base mt-1">
                  {clusterData.cluster_name_on_cloud ? (
                    <NonCapitalizedTooltip
                      content={`Name on ${clusterData.cloud || clusterData.infra?.split('(')[0]?.trim() || 'cloud'}: ${clusterData.cluster_name_on_cloud}`}
                      className="text-sm text-muted-foreground"
                    >
                      <span className="border-b border-dotted border-gray-400 cursor-help">
                        {clusterData.cluster || clusterData.name}
                      </span>
                    </NonCapitalizedTooltip>
                  ) : (
                    clusterData.cluster || clusterData.name
                  )}
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
                  <PluginSlot
                    name="clusters.detail.last_event"
                    context={{ last_event: clusterData.last_event }}
                    fallback={
                      <NonCapitalizedTooltip
                        content={clusterData.last_event || '-'}
                        className="text-sm text-muted-foreground"
                      >
                        <span>{clusterData.last_event || '-'}</span>
                      </NonCapitalizedTooltip>
                    }
                  />
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

              {/* Queue Details section - right column */}
              {clusterData.details && (
                <PluginSlot
                  name="clusters.detail.queue_details"
                  context={{
                    details: clusterData.details,
                    queueName: clusterData.kueue_queue_name,
                    infra: clusterData.full_infra,
                    clusterData: clusterData,
                    title: 'Queue Details',
                  }}
                />
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

      {/* Plugin Slot: Cluster Infra Nodes */}
      <PluginSlot
        name="clusters.detail.nodes"
        context={{
          clusterHash: clusterData.cluster_hash,
          clusterName: clusterData.cluster,
          clusterNameOnCloud: clusterData.cluster_name_on_cloud,
          nodeNames: clusterData.node_names,
          numNodes: clusterData.num_nodes,
          infra: clusterData.full_infra,
          status: clusterData.status,
        }}
        wrapperClassName="mb-6"
      />

      {/* Jobs Table - Only show for active clusters */}
      {!isHistoricalCluster && (
        <div className="mb-8">
          <ClusterJobs
            clusterName={clusterData.cluster}
            clusterJobData={clusterJobData}
            loading={clusterJobsLoading}
            refreshClusterJobsOnly={refreshClusterJobsOnly}
            workspace={clusterData.workspace}
          />
        </div>
      )}

      {/* GPU Metrics Section - Show for all Kubernetes clusters (in-cluster and external), but not SSH node pools */}
      {clusterData &&
        clusterData.full_infra &&
        clusterData.full_infra.includes('Kubernetes') &&
        !clusterData.full_infra.includes('SSH') &&
        !clusterData.full_infra.includes('ssh') &&
        isGrafanaAvailable && (
          <div className="mb-6">
            <GPUMetricsSection
              clusterNameOnCloud={clusterData?.cluster_name_on_cloud}
              displayName={clusterData?.cluster}
              refreshTrigger={gpuMetricsRefreshTrigger}
              storageKey="skypilot-gpu-metrics-expanded"
            />
          </div>
        )}

      {/* Plugin Slot: Cluster Detail Events */}
      <PluginSlot
        name="clusters.detail.events"
        context={{
          clusterHash: clusterData.cluster_hash,
        }}
        wrapperClassName="mb-8"
      />
    </div>
  );
}

export default ClusterDetails;
