import React, { useState, useEffect, useCallback } from 'react';
import { CircularProgress } from '@mui/material';
import { useRouter } from 'next/router';
import Link from 'next/link';
import { StatusBadge } from '@/components/elements/StatusBadge';
import { getVolumes } from '@/data/connectors/volumes';
import dashboardCache from '@/lib/cache';
import {
  RotateCwIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  CopyIcon,
  CheckIcon,
} from 'lucide-react';
import {
  CustomTooltip as Tooltip,
  NonCapitalizedTooltip,
  formatFullTimestamp,
} from '@/components/utils';
import { useMobile } from '@/hooks/useMobile';
import Head from 'next/head';
import { formatYaml } from '@/lib/yamlUtils';
import { UserDisplay } from '@/components/elements/UserDisplay';
import { YamlHighlighter } from '@/components/YamlHighlighter';

function useVolumeDetails({ volumeName }) {
  const [volumeData, setVolumeData] = useState(null);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    if (!volumeName) return;
    setLoading(true);
    try {
      const volumes = await dashboardCache.get(getVolumes);
      const found = volumes.find((v) => v.name === volumeName);
      setVolumeData(found || null);
    } catch (error) {
      console.error('Failed to fetch volume details:', error);
      setVolumeData(null);
    } finally {
      setLoading(false);
    }
  }, [volumeName]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const refreshData = useCallback(async () => {
    dashboardCache.invalidate(getVolumes);
    await fetchData();
  }, [fetchData]);

  return { volumeData, loading, refreshData };
}

function VolumeDetails() {
  const router = useRouter();
  const { volume: volumeName } = router.query;

  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isInitialLoad, setIsInitialLoad] = useState(true);
  const isMobile = useMobile();
  const { volumeData, loading, refreshData } = useVolumeDetails({
    volumeName,
  });

  useEffect(() => {
    if (!loading && isInitialLoad) {
      setIsInitialLoad(false);
    }
  }, [loading, isInitialLoad]);

  const handleManualRefresh = async () => {
    setIsRefreshing(true);
    await refreshData();
    setIsRefreshing(false);
  };

  if (!router.isReady) {
    return <div>Loading...</div>;
  }

  const title = volumeName
    ? `Volume: ${volumeName} | SkyPilot Dashboard`
    : 'Volume Details | SkyPilot Dashboard';

  return (
    <>
      <Head>
        <title>{title}</title>
      </Head>
      <>
        <div className="flex items-center justify-between mb-4 h-5">
          <div className="text-base flex items-center">
            <Link href="/volumes" className="text-sky-blue hover:underline">
              Volumes
            </Link>
            <span className="mx-2 text-gray-500">&rsaquo;</span>
            <Link
              href={`/volumes/${encodeURIComponent(volumeName)}`}
              className="text-sky-blue hover:underline"
            >
              {volumeName}
            </Link>
          </div>

          <div className="text-sm flex items-center">
            {(loading || isRefreshing) && (
              <div className="flex items-center mr-4">
                <CircularProgress size={15} className="mt-0" />
                <span className="ml-2 text-gray-500">Loading...</span>
              </div>
            )}
            {volumeData && (
              <Tooltip
                content="Refresh"
                className="text-sm text-muted-foreground"
              >
                <button
                  onClick={handleManualRefresh}
                  disabled={loading || isRefreshing}
                  className="text-sky-blue hover:text-sky-blue-bright font-medium inline-flex items-center"
                >
                  <RotateCwIcon className="w-4 h-4 mr-1.5" />
                  {!isMobile && <span>Refresh</span>}
                </button>
              </Tooltip>
            )}
          </div>
        </div>

        {loading && isInitialLoad ? (
          <div className="flex justify-center items-center py-12">
            <CircularProgress size={24} className="mr-2" />
            <span className="text-gray-500">Loading volume details...</span>
          </div>
        ) : volumeData ? (
          <VolumeDetailCard volumeData={volumeData} />
        ) : (
          <div className="flex justify-center items-center py-12">
            <span className="text-gray-500">Volume not found.</span>
          </div>
        )}
      </>
    </>
  );
}

function UsedBySection({ clusters, pods }) {
  const clusterList = Array.isArray(clusters) ? clusters : [];
  const podList = Array.isArray(pods) ? pods : [];

  // Prioritize clusters over pods, matching UsedByCell in volumes table
  const isCluster = clusterList.length > 0;
  const items = isCluster ? clusterList : podList;

  if (items.length === 0) {
    return <span>N/A</span>;
  }

  return (
    <div className="space-y-1">
      <div>
        {items.map((name, idx) => (
          <span key={name}>
            {isCluster ? (
              <Link
                href={`/clusters/${encodeURIComponent(name)}`}
                className="text-blue-600 hover:underline"
              >
                {name}
              </Link>
            ) : (
              name
            )}
            {idx < items.length - 1 ? ', ' : ''}
          </span>
        ))}
      </div>
    </div>
  );
}

function VolumeDetailCard({ volumeData }) {
  const [isYamlExpanded, setIsYamlExpanded] = useState(false);
  const [isCopied, setIsCopied] = useState(false);
  const [isCommandCopied, setIsCommandCopied] = useState(false);

  const formattedYaml = React.useMemo(
    () =>
      volumeData.creation_yaml ? formatYaml(volumeData.creation_yaml) : null,
    [volumeData.creation_yaml]
  );

  const toggleYamlExpanded = () => {
    setIsYamlExpanded(!isYamlExpanded);
  };

  const copyYamlToClipboard = async () => {
    try {
      if (!formattedYaml) return;
      await navigator.clipboard.writeText(formattedYaml);
      setIsCopied(true);
      setTimeout(() => setIsCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy YAML to clipboard:', err);
    }
  };

  const copyCommandToClipboard = async () => {
    try {
      const command = volumeData.last_use;
      if (!command) return;
      await navigator.clipboard.writeText(command);
      setIsCommandCopied(true);
      setTimeout(() => setIsCommandCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy command to clipboard:', err);
    }
  };

  const hasCreationYaml = Boolean(formattedYaml);
  const hasCreationArtifacts = hasCreationYaml || volumeData.last_use;

  return (
    <div>
      {/* Volume Info Card */}
      <div className="mb-6">
        <div className="rounded-lg border bg-card text-card-foreground shadow-sm">
          <div className="flex items-center justify-between px-4 pt-4">
            <h3 className="text-lg font-semibold">Details</h3>
          </div>
          <div className="p-4">
            <div className="grid grid-cols-2 gap-6">
              <div>
                <div className="text-gray-600 font-medium text-base">
                  Status
                </div>
                <div className="text-base mt-1">
                  <StatusBadge
                    status={volumeData.status}
                    statusTooltip={
                      volumeData.error_message || volumeData.status
                    }
                  />
                </div>
              </div>
              <div>
                <div className="text-gray-600 font-medium text-base">Name</div>
                <div className="text-base mt-1">{volumeData.name}</div>
              </div>
              <div>
                <div className="text-gray-600 font-medium text-base">Infra</div>
                <div className="text-base mt-1">
                  {volumeData.infra ? (
                    <NonCapitalizedTooltip
                      content={volumeData.infra}
                      className="text-sm text-muted-foreground"
                    >
                      <span>
                        <Link
                          href="/infra"
                          className="text-blue-600 hover:underline"
                        >
                          {volumeData.cloud || volumeData.infra}
                        </Link>
                        {volumeData.region && (
                          <span className="text-gray-600">
                            {' '}
                            ({volumeData.region}
                            {volumeData.zone ? `/${volumeData.zone}` : ''})
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
                <div className="text-gray-600 font-medium text-base">Type</div>
                <div className="text-base mt-1">{volumeData.type || 'N/A'}</div>
              </div>
              <div>
                <div className="text-gray-600 font-medium text-base">Size</div>
                <div className="text-base mt-1">{volumeData.size || 'N/A'}</div>
              </div>
              <div>
                <div className="text-gray-600 font-medium text-base">User</div>
                <div className="text-base mt-1">
                  <UserDisplay
                    username={volumeData.user_name}
                    userHash={volumeData.user_hash}
                  />
                </div>
              </div>
              <div>
                <div className="text-gray-600 font-medium text-base">
                  Created
                </div>
                <div className="text-base mt-1">
                  {volumeData.launched_at
                    ? formatFullTimestamp(
                        new Date(volumeData.launched_at * 1000)
                      )
                    : 'N/A'}
                </div>
              </div>
              <div>
                <div className="text-gray-600 font-medium text-base">
                  Last Use
                </div>
                <div className="text-base mt-1">
                  {volumeData.last_attached_at
                    ? formatFullTimestamp(
                        new Date(volumeData.last_attached_at * 1000)
                      )
                    : 'N/A'}
                </div>
              </div>
              <div>
                <div className="text-gray-600 font-medium text-base">
                  Name on Cloud
                </div>
                <div className="text-base mt-1">
                  {volumeData.name_on_cloud || 'N/A'}
                </div>
              </div>
              <div>
                <div className="text-gray-600 font-medium text-base">
                  Storage Class
                </div>
                <div className="text-base mt-1">
                  {volumeData.storage_class || 'N/A'}
                </div>
              </div>
              <div>
                <div className="text-gray-600 font-medium text-base">
                  Access Mode
                </div>
                <div className="text-base mt-1">
                  {volumeData.access_mode || 'N/A'}
                </div>
              </div>
              <div>
                <div className="text-gray-600 font-medium text-base">
                  Namespace
                </div>
                <div className="text-base mt-1">
                  {volumeData.namespace || 'N/A'}
                </div>
              </div>

              {/* Used By - spans both columns */}
              <div className="col-span-2">
                <div className="text-gray-600 font-medium text-base">
                  Used By
                </div>
                <div className="text-base mt-1">
                  <UsedBySection
                    clusters={volumeData.usedby_clusters}
                    pods={volumeData.usedby_pods}
                  />
                </div>
              </div>

              {/* Creation artifacts - spans both columns */}
              {hasCreationArtifacts && (
                <div className="col-span-2">
                  {/* Last Use command */}
                  {volumeData.last_use && (
                    <div>
                      <div className="flex items-center">
                        <div className="text-gray-600 font-medium text-base">
                          Entrypoint
                        </div>
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
                      </div>
                      <div className="bg-gray-50 border border-gray-200 rounded-md p-3 mt-2">
                        <code className="text-sm text-gray-800 font-mono break-all">
                          {volumeData.last_use}
                        </code>
                      </div>
                    </div>
                  )}

                  {/* Volume YAML - Collapsible */}
                  {hasCreationYaml && (
                    <div className="mt-4">
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
                          <span className="text-base">Show Volume YAML</span>
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
                            {formattedYaml}
                          </YamlHighlighter>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default VolumeDetails;
