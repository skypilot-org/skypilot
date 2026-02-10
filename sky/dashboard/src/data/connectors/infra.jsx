import { CLOUDS_LIST, COMMON_GPUS } from '@/data/connectors/constants';

// Importing from the same directory
import { apiClient } from '@/data/connectors/client';
import { getErrorMessageFromResponse } from '@/data/utils';
import dashboardCache from '@/lib/cache';
import { buildContextStatsKeyFromCloud } from '@/utils/infraUtils';

/**
 * Fast function to get just the list of enabled clouds (without counts).
 * Used for progressive loading - display cloud rows immediately, then overlay counts.
 */
export async function getEnabledCloudsList() {
  const { getWorkspaces, getEnabledClouds } = await import(
    '@/data/connectors/workspaces'
  );

  try {
    // Get workspaces (fast - cached)
    const workspacesData = await dashboardCache
      .get(getWorkspaces)
      .catch(() => ({}));
    const workspaceNames = Object.keys(workspacesData || {});

    if (workspaceNames.length === 0) {
      return { clouds: [], totalClouds: CLOUDS_LIST.length, enabledClouds: 0 };
    }

    // Fetch enabled clouds for each workspace and aggregate
    const enabledCloudsSet = new Set();

    await Promise.all(
      workspaceNames.map(async (workspaceName) => {
        try {
          const workspaceClouds = await dashboardCache.get(getEnabledClouds, [
            workspaceName,
            false,
          ]);
          if (Array.isArray(workspaceClouds)) {
            workspaceClouds.forEach((cloud) => {
              if (cloud) {
                enabledCloudsSet.add(cloud.toLowerCase());
              }
            });
          }
        } catch (error) {
          console.error(
            `Error fetching enabled clouds for workspace ${workspaceName}:`,
            error
          );
        }
      })
    );

    // Build cloud objects with just name and enabled status (no counts)
    const enabledCloudsList = Array.from(enabledCloudsSet);
    const clouds = CLOUDS_LIST.filter((cloud) =>
      enabledCloudsList.includes(cloud.toLowerCase())
    )
      .map((name) => ({ name, enabled: true }))
      .sort((a, b) => a.name.localeCompare(b.name));

    return {
      clouds,
      totalClouds: CLOUDS_LIST.length,
      enabledClouds: clouds.length,
    };
  } catch (error) {
    console.error('Error fetching enabled clouds list:', error);
    return { clouds: [], totalClouds: CLOUDS_LIST.length, enabledClouds: 0 };
  }
}

export async function getCloudInfrastructure(forceRefresh = false) {
  const { getClusters } = await import('@/data/connectors/clusters');
  const { getManagedJobs } = await import('@/data/connectors/jobs');
  const { getWorkspaces, getEnabledClouds } = await import(
    '@/data/connectors/workspaces'
  );

  try {
    // Fetch jobs, clusters, and workspaces in parallel for better performance
    const [jobsResult, clustersResult, workspacesData] = await Promise.all([
      // Use shared cache key (no field filtering) - preloader uses same args
      dashboardCache
        .get(getManagedJobs, [{ allUsers: true, skipFinished: true }])
        .catch((error) => {
          console.error('Error fetching managed jobs:', error);
          return { jobs: [] };
        }),
      dashboardCache.get(getClusters).catch((error) => {
        console.error('Error fetching clusters:', error);
        return [];
      }),
      dashboardCache.get(getWorkspaces).catch((error) => {
        console.error('Error fetching workspaces:', error);
        return {};
      }),
    ]);

    const jobs = jobsResult?.jobs || [];
    const clusters = clustersResult || [];

    // Get enabled clouds by aggregating across all workspaces
    let enabledCloudsList = [];
    const workspaceNames = Object.keys(workspacesData || {});

    if (workspaceNames.length === 0) {
      console.warn('No accessible workspaces found');
      enabledCloudsList = [];
    } else {
      // Fetch enabled clouds for each workspace and aggregate
      const enabledCloudsSet = new Set();

      await Promise.all(
        workspaceNames.map(async (workspaceName) => {
          try {
            const workspaceClouds = await dashboardCache.get(getEnabledClouds, [
              workspaceName,
              false,
            ]);
            if (Array.isArray(workspaceClouds)) {
              workspaceClouds.forEach((cloud) => {
                if (cloud) {
                  enabledCloudsSet.add(cloud.toLowerCase());
                }
              });
            }
          } catch (error) {
            console.error(
              `Error fetching enabled clouds for workspace ${workspaceName}:`,
              error
            );
          }
        })
      );

      enabledCloudsList = Array.from(enabledCloudsSet);
      console.log(
        'Aggregated enabled clouds across all workspaces:',
        enabledCloudsList
      );
    }

    // Create a map to store cloud data
    const cloudsData = {};

    // Initialize with all clouds from CLOUDS_LIST
    CLOUDS_LIST.forEach((cloud) => {
      // Check if the cloud is in the enabled clouds list
      const isEnabled = enabledCloudsList.includes(cloud.toLowerCase());

      cloudsData[cloud] = {
        name: cloud,
        clusters: 0,
        jobs: 0,
        enabled: isEnabled,
      };
    });

    // Count clusters per cloud
    clusters.forEach((cluster) => {
      if (cluster.cloud) {
        const cloudName = cluster.cloud;
        if (cloudsData[cloudName]) {
          cloudsData[cloudName].clusters += 1;
          // If we have clusters in a cloud, it must be enabled
          cloudsData[cloudName].enabled = true;
        }
      }
    });

    // Count jobs per cloud
    jobs.forEach((job) => {
      if (job.cloud) {
        const cloudName = job.cloud;
        if (cloudsData[cloudName]) {
          cloudsData[cloudName].jobs += 1;
          // If we have jobs in a cloud, it must be enabled
          cloudsData[cloudName].enabled = true;
        }
      }
    });

    // Get total and enabled counts for the UI
    const totalClouds = CLOUDS_LIST.length;
    const enabledClouds = Object.values(cloudsData).filter(
      (c) => c.enabled
    ).length;

    // Convert to array, filter to only enabled clouds, and sort by name
    const result = Object.values(cloudsData)
      .filter((cloud) => cloud.enabled)
      .sort((a, b) => a.name.localeCompare(b.name));

    return {
      clouds: result,
      totalClouds,
      enabledClouds,
    };
  } catch (error) {
    console.error('Error fetching cloud infrastructure:', error);
    throw error;
  }
}

export async function getGPUs() {
  // Legacy function - now redirects to workspace-aware infrastructure
  return await getWorkspaceInfrastructure();
}

// New workspace-aware infrastructure fetching function
export async function getWorkspaceInfrastructure() {
  try {
    console.log('[DEBUG] Starting workspace-aware infrastructure fetch');

    // Step 1: Get all accessible workspaces for the user (use cache for performance)
    const { getWorkspaces } = await import('@/data/connectors/workspaces');
    console.log('[DEBUG] About to call getWorkspaces() via cache');
    const workspacesData = await dashboardCache.get(getWorkspaces);
    console.log('[DEBUG] Workspaces data received:', workspacesData);
    console.log(
      '[DEBUG] Number of accessible workspaces:',
      Object.keys(workspacesData || {}).length
    );
    console.log('[DEBUG] Workspace names:', Object.keys(workspacesData || {}));

    if (!workspacesData || Object.keys(workspacesData).length === 0) {
      console.log(
        '[DEBUG] No accessible workspaces found - returning empty result'
      );
      return {
        workspaces: {},
        allContextNames: [],
        allGPUs: [],
        perContextGPUs: [],
        perNodeGPUs: [],
        allSlurmGPUs: [],
        perClusterSlurmGPUs: [],
        perNodeSlurmGPUs: [],
        contextStats: {},
        contextWorkspaceMap: {},
        contextErrors: {},
      };
    }

    // Step 2: For each workspace, fetch enabled clouds with expanded infrastructure
    const { getEnabledClouds } = await import('@/data/connectors/workspaces');
    const workspaceInfraData = {};
    const allContextsAcrossWorkspaces = [];
    const contextWorkspaceMap = {};

    await Promise.allSettled(
      Object.entries(workspacesData).map(
        async ([workspaceName, workspaceConfig]) => {
          console.log(
            `Fetching infrastructure for workspace: ${workspaceName}`
          );

          try {
            // Get enabled clouds with expanded infrastructure for this workspace (use cache for performance)
            console.log(
              `[DEBUG] Fetching enabled clouds for workspace: ${workspaceName} via cache`
            );
            const expandedClouds = await dashboardCache.get(getEnabledClouds, [
              workspaceName,
              true,
            ]);
            console.log(
              `[DEBUG] Expanded clouds for ${workspaceName}:`,
              expandedClouds
            );

            workspaceInfraData[workspaceName] = {
              config: workspaceConfig,
              clouds: expandedClouds,
              contexts: [],
            };

            // Extract contexts from expanded cloud data
            // expandedClouds is an array of strings like ['kubernetes/context1', 'SSH/pool1']
            console.log(
              `[DEBUG] Processing expandedClouds for ${workspaceName}:`,
              expandedClouds
            );
            if (expandedClouds && Array.isArray(expandedClouds)) {
              expandedClouds.forEach((infraItem) => {
                console.log(`[DEBUG] Processing infraItem: ${infraItem}`);
                if (infraItem.toLowerCase().startsWith('kubernetes/')) {
                  const context = infraItem.replace(/^kubernetes\//i, '');
                  console.log(
                    `[DEBUG] Extracted kubernetes context: ${context}`
                  );
                  allContextsAcrossWorkspaces.push(context);
                  if (!contextWorkspaceMap[context]) {
                    contextWorkspaceMap[context] = [];
                  }
                  if (!contextWorkspaceMap[context].includes(workspaceName)) {
                    contextWorkspaceMap[context].push(workspaceName);
                  }
                  workspaceInfraData[workspaceName].contexts.push(context);
                } else if (infraItem.toLowerCase().startsWith('ssh/')) {
                  const poolName = infraItem.replace(/^ssh\//i, '');
                  const sshContextName = `ssh-${poolName}`;
                  console.log(
                    `[DEBUG] Extracted SSH context: ${sshContextName}`
                  );
                  allContextsAcrossWorkspaces.push(sshContextName);
                  if (!contextWorkspaceMap[sshContextName]) {
                    contextWorkspaceMap[sshContextName] = [];
                  }
                  if (
                    !contextWorkspaceMap[sshContextName].includes(workspaceName)
                  ) {
                    contextWorkspaceMap[sshContextName].push(workspaceName);
                  }
                  workspaceInfraData[workspaceName].contexts.push(
                    sshContextName
                  );
                }
              });
            } else {
              console.log(
                `[DEBUG] No expanded clouds or not an array for ${workspaceName}`
              );
            }
          } catch (error) {
            console.error(
              `Failed to fetch infrastructure for workspace ${workspaceName}:`,
              error
            );
            throw error;
          }
        }
      )
    );

    // Step 3: Get detailed GPU information for all contexts
    const { getClusters } = await import('@/data/connectors/clusters');
    let clustersData = [];
    try {
      clustersData = await dashboardCache.get(getClusters);
    } catch (error) {
      console.error('Error fetching clusters:', error);
    }
    const clusters = clustersData || [];

    // Get context stats (cluster counts)
    let contextStats = {};
    try {
      contextStats = await getContextClusters(clusters);
    } catch (error) {
      console.error('Error fetching context clusters:', error);
    }

    // Get GPU data for all contexts (filter out any undefined contexts)
    const validContexts = [...new Set(allContextsAcrossWorkspaces)].filter(
      (context) => context && typeof context === 'string'
    );
    let gpuData = {
      allGPUs: [],
      perContextGPUs: [],
      perNodeGPUs: [],
      contextErrors: {},
    };
    try {
      gpuData = await getKubernetesGPUsFromContexts(validContexts);
    } catch (error) {
      console.error('Error fetching Kubernetes GPUs:', error);
    }

    // Note: Slurm GPU data is now fetched separately via getSlurmInfrastructure()
    // This allows Slurm to load in parallel with Kubernetes/SSH data

    const finalResult = {
      workspaces: workspaceInfraData,
      allContextNames: [...new Set(allContextsAcrossWorkspaces)].sort(),
      allGPUs: gpuData.allGPUs || [],
      perContextGPUs: gpuData.perContextGPUs || [],
      perNodeGPUs: gpuData.perNodeGPUs || [],
      contextStats: contextStats,
      contextWorkspaceMap: contextWorkspaceMap,
      contextErrors: gpuData.contextErrors || {},
    };

    console.log('[DEBUG] Final result:', finalResult);
    console.log('[DEBUG] All contexts found:', allContextsAcrossWorkspaces);
    console.log('[DEBUG] Context workspace map:', contextWorkspaceMap);

    return finalResult;
  } catch (error) {
    console.error('[DEBUG] Failed to fetch workspace infrastructure:', error);
    console.error('[DEBUG] Error stack:', error.stack);
    throw error;
  }
}

// Lightweight function to get just context names quickly (without GPU data)
// This allows the UI to show contexts immediately while GPU data loads progressively
export async function getWorkspaceContexts() {
  try {
    // Step 1: Get all accessible workspaces for the user (use cache for performance)
    const { getWorkspaces } = await import('@/data/connectors/workspaces');
    const workspacesData = await dashboardCache.get(getWorkspaces);

    if (!workspacesData || Object.keys(workspacesData).length === 0) {
      return {
        workspaces: {},
        allContextNames: [],
        contextWorkspaceMap: {},
      };
    }

    // Step 2: For each workspace, fetch enabled clouds with expanded infrastructure
    const { getEnabledClouds } = await import('@/data/connectors/workspaces');
    const workspaceInfraData = {};
    const allContextsAcrossWorkspaces = [];
    const contextWorkspaceMap = {};

    await Promise.allSettled(
      Object.entries(workspacesData).map(
        async ([workspaceName, workspaceConfig]) => {
          try {
            // Get enabled clouds with expanded infrastructure for this workspace
            const expandedClouds = await dashboardCache.get(getEnabledClouds, [
              workspaceName,
              true,
            ]);

            workspaceInfraData[workspaceName] = {
              config: workspaceConfig,
              clouds: expandedClouds,
              contexts: [],
            };

            // Extract contexts from expanded cloud data
            if (expandedClouds && Array.isArray(expandedClouds)) {
              expandedClouds.forEach((infraItem) => {
                if (infraItem.toLowerCase().startsWith('kubernetes/')) {
                  const context = infraItem.replace(/^kubernetes\//i, '');
                  allContextsAcrossWorkspaces.push(context);
                  if (!contextWorkspaceMap[context]) {
                    contextWorkspaceMap[context] = [];
                  }
                  if (!contextWorkspaceMap[context].includes(workspaceName)) {
                    contextWorkspaceMap[context].push(workspaceName);
                  }
                  workspaceInfraData[workspaceName].contexts.push(context);
                } else if (infraItem.toLowerCase().startsWith('ssh/')) {
                  const poolName = infraItem.replace(/^ssh\//i, '');
                  const sshContextName = `ssh-${poolName}`;
                  allContextsAcrossWorkspaces.push(sshContextName);
                  if (!contextWorkspaceMap[sshContextName]) {
                    contextWorkspaceMap[sshContextName] = [];
                  }
                  if (
                    !contextWorkspaceMap[sshContextName].includes(workspaceName)
                  ) {
                    contextWorkspaceMap[sshContextName].push(workspaceName);
                  }
                  workspaceInfraData[workspaceName].contexts.push(
                    sshContextName
                  );
                }
              });
            }
          } catch (error) {
            console.error(
              `Failed to fetch infrastructure for workspace ${workspaceName}:`,
              error
            );
          }
        }
      )
    );

    return {
      workspaces: workspaceInfraData,
      allContextNames: [...new Set(allContextsAcrossWorkspaces)].sort(),
      contextWorkspaceMap: contextWorkspaceMap,
    };
  } catch (error) {
    console.error('Failed to fetch workspace contexts:', error);
    throw error;
  }
}

// Fetch GPU data for a single context - used for progressive loading
// Returns processed GPU data for one context that can be merged into state
export async function getContextGPUData(context) {
  try {
    const nodeInfoDict = await getKubernetesPerNodeGPUs(context);

    // Process node info into GPU summaries
    const gpuToData = {};
    const perNodeGPUs = [];

    if (nodeInfoDict && Object.keys(nodeInfoDict).length > 0) {
      for (const nodeName in nodeInfoDict) {
        const nodeData = nodeInfoDict[nodeName];
        if (!nodeData) continue;

        const gpuName = nodeData['accelerator_type'] || '-';
        const totalCount = nodeData['total']?.['accelerator_count'] || 0;
        const freeCount = nodeData['free']?.['accelerators_available'] || 0;
        const isReady = nodeData['is_ready'] !== false;
        // Check if node is cordoned (defaults to false for backward compatibility)
        const isCordoned = nodeData['is_cordoned'] === true;
        // Check if node has taints (defaults to empty for backward compatibility)
        const taints = nodeData['taints'] || [];
        const isTainted = taints.length > 0;
        // Node is considered not ready if it's not ready, cordoned, or tainted
        const isNodeNotReady = !isReady || isCordoned || isTainted;

        // Per-node data - use same field names as original getKubernetesGPUsFromContexts
        perNodeGPUs.push({
          node_name: nodeData['name'] || nodeName,
          gpu_name: gpuName,
          gpu_total: totalCount,
          gpu_free: freeCount,
          is_ready: isReady,
          is_cordoned: isCordoned,
          taints: taints,
          context: context,
          ip_address: nodeData['ip_address'] || null,
          cpu_count: nodeData['cpu_count'] ?? null,
          memory_gb: nodeData['memory_gb'] ?? null,
          cpu_free: nodeData['cpu_free'] ?? null,
          memory_free_gb: nodeData['memory_free_gb'] ?? null,
        });

        // Aggregate GPU data per context
        if (totalCount > 0) {
          if (!gpuToData[gpuName]) {
            gpuToData[gpuName] = {
              gpu_name: gpuName,
              gpu_requestable_qty_per_node: 0,
              gpu_total: 0,
              gpu_free: 0,
              gpu_not_ready: 0,
              context: context,
            };
          }
          gpuToData[gpuName].gpu_total += totalCount;
          gpuToData[gpuName].gpu_free += freeCount;
          if (isNodeNotReady) {
            gpuToData[gpuName].gpu_not_ready += totalCount;
          }
          gpuToData[gpuName].gpu_requestable_qty_per_node = totalCount;
        }
      }
    }

    return {
      context,
      perContextGPUs: Object.values(gpuToData),
      perNodeGPUs: perNodeGPUs,
      error: null,
    };
  } catch (error) {
    const errorMessage =
      error?.message ||
      (typeof error === 'string' && error) ||
      'Context may be unavailable or timed out';
    console.warn(
      `Failed to get GPU data for context ${context}:`,
      errorMessage
    );
    return {
      context,
      perContextGPUs: [],
      perNodeGPUs: [],
      error: errorMessage,
    };
  }
}

// Helper function to get GPU data for specific contexts
async function getKubernetesGPUsFromContexts(contextNames) {
  try {
    if (!contextNames || contextNames.length === 0) {
      return {
        allGPUs: [],
        perContextGPUs: [],
        perNodeGPUs: [],
        contextErrors: {},
      };
    }

    const allGPUsSummary = {};
    const perContextGPUsData = {};
    const perNodeGPUs_dict = {};
    const contextErrors = {};

    // Get all of the node info for all contexts in parallel and put them
    // in a dictionary keyed by context name.
    // Use Promise.allSettled to handle partial failures gracefully
    const contextNodeInfoResults = await Promise.allSettled(
      contextNames.map((context) => getKubernetesPerNodeGPUs(context))
    );
    const contextToNodeInfo = {};
    for (let i = 0; i < contextNames.length; i++) {
      const result = contextNodeInfoResults[i];
      if (result.status === 'fulfilled') {
        contextToNodeInfo[contextNames[i]] = result.value;
        console.log(
          '[CONTEXT_DEBUG] Context node info result:',
          contextNames[i],
          result.value
        );
      } else {
        // Log the error but continue with other contexts
        const errorMessage =
          result.reason?.message ||
          (typeof result.reason === 'string' && result.reason) ||
          'Context may be unavailable or timed out';
        console.warn(
          `Failed to get node info for context ${contextNames[i]}:`,
          errorMessage
        );
        contextToNodeInfo[contextNames[i]] = {};
        contextErrors[contextNames[i]] = errorMessage;
      }
    }

    // Populate the gpuToData map for each context.
    for (const context of contextNames) {
      const nodeInfoForContext = contextToNodeInfo[context] || {};
      if (nodeInfoForContext && Object.keys(nodeInfoForContext).length > 0) {
        const gpuToData = {};
        for (const nodeName in nodeInfoForContext) {
          const nodeData = nodeInfoForContext[nodeName];
          if (!nodeData) {
            console.warn(
              `No node data for node ${nodeName} in context ${context}`
            );
            continue;
          }

          const gpuName = nodeData['accelerator_type'] || '-';
          const totalCount = nodeData['total']?.['accelerator_count'] || 0;
          const freeCount = nodeData['free']?.['accelerators_available'] || 0;
          // Check if node is ready (defaults to true for backward compatibility)
          const isReady = nodeData['is_ready'] !== false;
          // Check if node is cordoned (defaults to false for backward compatibility)
          const isCordoned = nodeData['is_cordoned'] === true;
          // Check if node has taints (defaults to empty for backward compatibility)
          const taints = nodeData['taints'] || [];
          const isTainted = taints.length > 0;
          // Node is considered not ready if it's not ready, cordoned, or tainted
          const isNodeNotReady = !isReady || isCordoned || isTainted;

          if (totalCount > 0) {
            if (!gpuToData[gpuName]) {
              gpuToData[gpuName] = {
                gpu_name: gpuName,
                gpu_requestable_qty_per_node: 0,
                gpu_total: 0,
                gpu_free: 0,
                gpu_not_ready: 0,
                context: context,
              };
            }
            gpuToData[gpuName].gpu_total += totalCount;
            gpuToData[gpuName].gpu_free += freeCount;
            if (isNodeNotReady) {
              gpuToData[gpuName].gpu_not_ready += totalCount;
            }
            gpuToData[gpuName].gpu_requestable_qty_per_node = totalCount;
          }
        }
        perContextGPUsData[context] = Object.values(gpuToData);
        for (const gpuName in gpuToData) {
          if (gpuName in allGPUsSummary) {
            allGPUsSummary[gpuName].gpu_total += gpuToData[gpuName].gpu_total;
            allGPUsSummary[gpuName].gpu_free += gpuToData[gpuName].gpu_free;
            allGPUsSummary[gpuName].gpu_not_ready +=
              gpuToData[gpuName].gpu_not_ready;
          } else {
            allGPUsSummary[gpuName] = {
              gpu_total: gpuToData[gpuName].gpu_total,
              gpu_free: gpuToData[gpuName].gpu_free,
              gpu_not_ready: gpuToData[gpuName].gpu_not_ready,
              gpu_name: gpuName,
            };
          }
        }
      } else {
        // Initialize empty array for contexts that don't have node info
        perContextGPUsData[context] = [];
      }
    }

    // Populate the perNodeGPUs_dict map for each context.
    for (const context of contextNames) {
      const nodeInfoForContext = contextToNodeInfo[context];
      if (nodeInfoForContext && Object.keys(nodeInfoForContext).length > 0) {
        for (const nodeName in nodeInfoForContext) {
          const nodeData = nodeInfoForContext[nodeName];
          if (!nodeData) {
            console.warn(
              `No node data for node ${nodeName} in context ${context}`
            );
            continue;
          }

          // Ensure accelerator_type, total, and free fields exist or provide defaults
          const acceleratorType = nodeData['accelerator_type'] || '-';
          const totalAccelerators =
            nodeData['total']?.['accelerator_count'] ?? 0;
          const freeAccelerators =
            nodeData['free']?.['accelerators_available'] ?? 0;
          // Check if node is ready (defaults to true for backward compatibility)
          const nodeIsReady = nodeData['is_ready'] !== false;
          // Check if node is cordoned (defaults to false for backward compatibility)
          const nodeIsCordoned = nodeData['is_cordoned'] === true;
          // Get taints (defaults to empty for backward compatibility)
          const nodeTaints = nodeData['taints'] || [];

          // Extract CPU and memory information
          const cpuCount = nodeData['cpu_count'] ?? null;
          const memoryGb = nodeData['memory_gb'] ?? null;
          const cpuFree = nodeData['cpu_free'] ?? null;
          const memoryFreeGb = nodeData['memory_free_gb'] ?? null;

          perNodeGPUs_dict[`${context}/${nodeName}`] = {
            node_name: nodeData['name'] || nodeName,
            gpu_name: acceleratorType,
            gpu_total: totalAccelerators,
            gpu_free: freeAccelerators,
            ip_address: nodeData['ip_address'] || null,
            context: context,
            cpu_count: cpuCount,
            memory_gb: memoryGb,
            cpu_free: cpuFree,
            memory_free_gb: memoryFreeGb,
            is_ready: nodeIsReady,
            is_cordoned: nodeIsCordoned,
            taints: nodeTaints,
          };

          // If this node provides a GPU type not found via GPU availability,
          // add it to perContextGPUsData with 0/0 counts if it's not already there.
          if (
            acceleratorType !== '-' &&
            perContextGPUsData[context] &&
            !perContextGPUsData[context].some(
              (gpu) => gpu.gpu_name === acceleratorType
            )
          ) {
            if (!(acceleratorType in allGPUsSummary)) {
              allGPUsSummary[acceleratorType] = {
                gpu_total: 0,
                gpu_free: 0,
                gpu_not_ready: 0,
                gpu_name: acceleratorType,
              };
            }
            const existingGpuEntry = perContextGPUsData[context].find(
              (gpu) => gpu.gpu_name === acceleratorType
            );
            if (!existingGpuEntry) {
              perContextGPUsData[context].push({
                gpu_name: acceleratorType,
                gpu_not_ready: 0,
                gpu_requestable_qty_per_node: '-',
                gpu_total: 0,
                gpu_free: 0,
                context: context,
              });
            }
          }
        }
      }
    }

    console.log('[CONTEXT_DEBUG] All GPUs summary:', allGPUsSummary);
    console.log('[CONTEXT_DEBUG] Per context GPUs data:', perContextGPUsData);
    console.log('[CONTEXT_DEBUG] Per node GPUs data:', perNodeGPUs_dict);
    console.log('[CONTEXT_DEBUG] Context errors:', contextErrors);
    return {
      allGPUs: Object.values(allGPUsSummary).sort((a, b) =>
        (a.gpu_name || '').localeCompare(b.gpu_name || '')
      ),
      perContextGPUs: Object.values(perContextGPUsData)
        .flat()
        .sort(
          (a, b) =>
            (a.context || '').localeCompare(b.context || '') ||
            (a.gpu_name || '').localeCompare(b.gpu_name || '')
        ),
      perNodeGPUs: Object.values(perNodeGPUs_dict).sort(
        (a, b) =>
          (a.context || '').localeCompare(b.context || '') ||
          (a.node_name || '').localeCompare(b.node_name || '') ||
          (a.gpu_name || '').localeCompare(b.gpu_name || '')
      ),
      contextErrors: contextErrors,
    };
  } catch (error) {
    console.error('[infra.jsx] Error in getKubernetesGPUsFromContexts:', error);
    throw error;
  }
}

async function getKubernetesPerNodeGPUs(context) {
  try {
    const response = await apiClient.post(`/kubernetes_node_info`, {
      context: context,
    });
    if (!response.ok) {
      const msg = `Failed to get kubernetes node info for context ${context} with status ${response.status}, error: ${response.statusText}`;
      throw new Error(msg);
    }
    const id = response.headers.get('X-Skypilot-Request-ID');
    if (!id) {
      const msg = 'No request ID received from server for kubernetes node info';
      throw new Error(msg);
    }
    const fetchedData = await apiClient.get(`/api/get?request_id=${id}`);
    if (!fetchedData.ok) {
      const errorMessage = await getErrorMessageFromResponse(fetchedData);
      const msg = `Failed to get kubernetes node info result for context ${context} with status ${fetchedData.status}, error: ${errorMessage}`;
      throw new Error(msg);
    }
    const data = await fetchedData.json();
    const nodeInfo = data.return_value ? JSON.parse(data.return_value) : {};
    const nodeInfoDict = nodeInfo['node_info_dict'] || {};
    return nodeInfoDict;
  } catch (error) {
    console.warn(
      `[infra.jsx] Context ${context} unavailable or timed out:`,
      error.message
    );
    throw error;
  }
}

export async function getContextJobs(jobs) {
  try {
    // Count jobs per k8s context/ssh node pool/slurm cluster
    const contextStats = {};

    // Process jobs
    jobs.forEach((job) => {
      const contextKey = buildContextStatsKeyFromCloud(job.cloud, job.region);

      if (contextKey) {
        if (!contextStats[contextKey]) {
          contextStats[contextKey] = { clusters: 0, jobs: 0 };
        }
        contextStats[contextKey].jobs += 1;
      }
    });

    return contextStats;
  } catch (error) {
    console.error('=== Error in getContextJobs ===', error);
    throw error;
  }
}

export async function getContextClusters(clusters) {
  try {
    // Count clusters per k8s context/ssh node pool/slurm cluster
    const contextStats = {};
    clusters.forEach((cluster) => {
      const contextKey = buildContextStatsKeyFromCloud(
        cluster.cloud,
        cluster.region
      );

      if (contextKey) {
        if (!contextStats[contextKey]) {
          contextStats[contextKey] = { clusters: 0, jobs: 0 };
        }
        contextStats[contextKey].clusters += 1;
      }
    });

    return contextStats;
  } catch (error) {
    console.error('=== Error in getContextClusters ===', error);
    throw error;
  }
}

export async function getCloudGPUs() {
  try {
    const response = await apiClient.post(`/list_accelerator_counts`, {
      clouds: CLOUDS_LIST,
      gpus_only: true,
    });
    if (!response.ok) {
      const msg = `Failed to get cloud GPUs with status ${response.status}, error: ${response.statusText}`;
      throw new Error(msg);
    }
    const id = response.headers.get('X-Skypilot-Request-ID');
    if (!id) {
      const msg = 'No request ID received from server for cloud GPUs';
      throw new Error(msg);
    }
    const fetchedData = await apiClient.get(`/api/get?request_id=${id}`);
    if (!fetchedData.ok) {
      const errorMessage = await getErrorMessageFromResponse(fetchedData);
      const msg = `Failed to get cloud GPUs result with status ${fetchedData.status}, error: ${errorMessage}`;
      throw new Error(msg);
    }
    const data = await fetchedData.json();
    const allGPUs = data.return_value ? JSON.parse(data.return_value) : {};
    const commonGPUs = Object.keys(allGPUs)
      .filter((gpu) => COMMON_GPUS.includes(gpu))
      .map((gpu) => ({
        gpu_name: gpu,
        gpu_quantities: allGPUs[gpu].join(', '),
      }))
      .sort((a, b) => a.gpu_name.localeCompare(b.gpu_name));
    const tpus = Object.keys(allGPUs)
      .filter((gpu) => gpu.startsWith('tpu-'))
      .map((gpu) => ({
        gpu_name: gpu,
        gpu_quantities: allGPUs[gpu].join(', '),
      }))
      .sort((a, b) => a.gpu_name.localeCompare(b.gpu_name));
    const otherGPUs = Object.keys(allGPUs)
      .filter((gpu) => !COMMON_GPUS.includes(gpu) && !gpu.startsWith('tpu-'))
      .map((gpu) => ({
        gpu_name: gpu,
        gpu_quantities: allGPUs[gpu].join(', '),
      }))
      .sort((a, b) => a.gpu_name.localeCompare(b.gpu_name));
    return {
      commonGPUs,
      tpus,
      otherGPUs,
    };
  } catch (error) {
    console.error('Error fetching cloud GPUs:', error);
    throw error;
  }
}

export async function getDetailedGpuInfo(filter) {
  try {
    let gpuName = filter;
    let gpuCount = null;

    if (filter.includes(':')) {
      const [name, countStr] = filter.split(':');
      gpuName = name.trim();
      const parsedCount = parseInt(countStr.trim());
      if (!isNaN(parsedCount) && parsedCount > 0) {
        gpuCount = parsedCount;
      }
    }

    console.log(
      `Searching for GPU: ${gpuName}${gpuCount !== null ? ', effective count: ${gpuCount}' : ''}`
    );

    const response = await apiClient.post(`/list_accelerators`, {
      gpus_only: true,
      name_filter: gpuName,
      quantity_filter: gpuCount,
      clouds: CLOUDS_LIST,
      case_sensitive: false,
      all_regions: true,
    });
    if (!response.ok) {
      const msg = `Failed to get detailed GPU info with status ${response.status}, error: ${response.statusText}`;
      throw new Error(msg);
    }
    const id = response.headers.get('X-Skypilot-Request-ID');
    if (!id) {
      const msg = 'No request ID received from server for detailed GPU info';
      throw new Error(msg);
    }
    const fetchedData = await apiClient.get(`/api/get?request_id=${id}`);
    if (!fetchedData.ok) {
      const errorMessage = await getErrorMessageFromResponse(fetchedData);
      const msg = `Failed to get detailed GPU info result with status ${fetchedData.status}, error: ${errorMessage}`;
      throw new Error(msg);
    }

    const data = await fetchedData.json();

    if (!data.return_value) {
      console.log('No return_value in API response for detailed GPU info.');
      return [];
    }

    let rawData;
    try {
      const jsonStr = data.return_value;
      const processedStr = jsonStr
        .replace(/NaN/g, 'null')
        .replace(/Infinity/g, 'null')
        .replace(/-Infinity/g, 'null')
        .replace(/undefined/g, 'null');

      rawData = JSON.parse(processedStr);
      console.log(
        'Successfully parsed GPU data. Top-level keys:',
        Object.keys(rawData)
      );
    } catch (parseError) {
      console.error('Error parsing GPU data:', parseError);
      throw parseError;
    }

    const formattedData = [];
    const expectedArrayLength = 10;

    for (const [gpuNameKey, instances] of Object.entries(rawData)) {
      if (!Array.isArray(instances)) {
        console.log(`Value for key ${gpuNameKey} is not an array:`, instances);
        continue;
      }
      console.log(`Processing ${instances.length} instances for ${gpuNameKey}`);
      if (instances.length > 0 && Array.isArray(instances[0])) {
        console.log(
          'First instance array being processed:',
          JSON.stringify(instances[0], null, 2)
        );
      } else if (instances.length > 0) {
        console.log(
          'First instance (not an array as expected):',
          JSON.stringify(instances[0], null, 2)
        );
      }

      instances.forEach((instanceArray) => {
        if (
          !Array.isArray(instanceArray) ||
          instanceArray.length < expectedArrayLength
        ) {
          if (!Array.isArray(instanceArray)) {
            console.warn(
              `Expected an array for instance under ${gpuNameKey}, but got:`,
              instanceArray
            );
            return;
          } else {
            console.warn(
              `Instance array for ${gpuNameKey} has unexpected length ${instanceArray.length} (expected ${expectedArrayLength}):`,
              instanceArray
            );
          }
        }

        const cloud = instanceArray[0];
        const instance_type = instanceArray[1];
        const acc_count = instanceArray[3];
        const cpu_val = instanceArray[4];
        const dev_mem_val = instanceArray[5];
        const mem_val = instanceArray[6];
        const price_val = instanceArray[7];
        const spot_val = instanceArray[8];
        const region_val = instanceArray[9];

        let display_count = acc_count;
        if (
          gpuCount !== null &&
          (display_count === null ||
            display_count === undefined ||
            display_count === 0)
        ) {
          display_count = gpuCount;
        }
        display_count =
          display_count === null ||
          display_count === undefined ||
          isNaN(parseInt(display_count))
            ? 0
            : parseInt(display_count);

        const instanceType = instance_type || '(attachable)';
        const deviceMemory =
          dev_mem_val !== null && !isNaN(dev_mem_val)
            ? `${Math.floor(dev_mem_val)}GB`
            : '-';
        const cpuCount =
          cpu_val !== null && !isNaN(cpu_val)
            ? Number.isInteger(cpu_val)
              ? cpu_val
              : parseFloat(cpu_val).toFixed(1)
            : '-';
        const memory =
          mem_val !== null && !isNaN(mem_val)
            ? `${Math.floor(mem_val)}GB`
            : '-';
        const price =
          price_val !== null && !isNaN(price_val)
            ? `$${parseFloat(price_val).toFixed(3)}`
            : '-';
        const spotPrice =
          spot_val !== null && !isNaN(spot_val)
            ? `$${parseFloat(spot_val).toFixed(3)}`
            : '-';
        const region = region_val || '-';

        formattedData.push({
          accelerator_name: gpuNameKey,
          accelerator_count: display_count,
          cloud: cloud || '',
          instance_type: instanceType,
          device_memory: deviceMemory,
          cpu_count: cpuCount,
          memory: memory,
          price: price,
          spot_price: spotPrice,
          region: region,
          raw_price:
            price_val !== null && !isNaN(price_val)
              ? parseFloat(price_val)
              : Infinity,
          raw_spot_price:
            spot_val !== null && !isNaN(spot_val)
              ? parseFloat(spot_val)
              : Infinity,
        });
      });
    }

    return formattedData.sort((a, b) => {
      if (a.raw_price !== b.raw_price) return a.raw_price - b.raw_price;
      return a.raw_spot_price - b.raw_spot_price;
    });
  } catch (error) {
    console.error('Outer error in getDetailedGpuInfo:', error);
    throw error;
  }
}

async function getSlurmClusterGPUs() {
  try {
    const response = await apiClient.post(`/slurm_gpu_availability`, {});
    if (!response.ok) {
      const msg = `Failed to get slurm cluster GPUs with status ${response.status}`;
      throw new Error(msg);
    }
    const id = response.headers.get('X-Skypilot-Request-ID');
    if (!id) {
      const msg = 'No request ID received from server for slurm cluster GPUs';
      throw new Error(msg);
    }
    const fetchedData = await apiClient.get(`/api/get?request_id=${id}`);
    if (fetchedData.status === 500) {
      try {
        const data = await fetchedData.json();
        if (data.detail && data.detail.error) {
          try {
            const error = JSON.parse(data.detail.error);
            console.error('Error fetching Slurm cluster GPUs:', error.message);
          } catch (jsonError) {
            console.error('Error parsing JSON for Slurm error:', jsonError);
          }
        }
      } catch (parseError) {
        console.error('Error parsing JSON for Slurm 500 response:', parseError);
      }
      return [];
    }
    if (!fetchedData.ok) {
      const msg = `Failed to get slurm cluster GPUs result with status ${fetchedData.status}`;
      throw new Error(msg);
    }
    const data = await fetchedData.json();
    const clusterGPUs = data.return_value ? JSON.parse(data.return_value) : [];
    return clusterGPUs;
  } catch (error) {
    console.error('Error fetching Slurm cluster GPUs:', error);
    return [];
  }
}

async function getSlurmPerNodeGPUs() {
  try {
    const response = await apiClient.post(`/slurm_node_info`, {});
    if (!response.ok) {
      const msg = `Failed to get slurm node info with status ${response.status}`;
      throw new Error(msg);
    }
    const id = response.headers.get('X-Skypilot-Request-ID');
    if (!id) {
      const msg = 'No request ID received from server for slurm node info';
      throw new Error(msg);
    }
    const fetchedData = await apiClient.get(`/api/get?request_id=${id}`);
    if (fetchedData.status === 500) {
      try {
        const data = await fetchedData.json();
        if (data.detail && data.detail.error) {
          try {
            const error = JSON.parse(data.detail.error);
            console.error('Error fetching Slurm per node GPUs:', error.message);
          } catch (jsonError) {
            console.error(
              'Error parsing JSON for Slurm node error:',
              jsonError
            );
          }
        }
      } catch (parseError) {
        console.error(
          'Error parsing JSON for Slurm node 500 response:',
          parseError
        );
      }
      return [];
    }
    if (!fetchedData.ok) {
      const msg = `Failed to get slurm node info result with status ${fetchedData.status}`;
      throw new Error(msg);
    }
    const data = await fetchedData.json();
    const nodeInfo = data.return_value ? JSON.parse(data.return_value) : [];
    return nodeInfo;
  } catch (error) {
    console.error('Error fetching Slurm per node GPUs:', error);
    return [];
  }
}

// Export Slurm infrastructure fetching for parallel loading
export async function getSlurmInfrastructure() {
  return await getSlurmServiceGPUs();
}

async function getSlurmServiceGPUs() {
  try {
    // Fetch cluster GPUs and node GPUs in parallel for better performance
    const [clusterGPUsRaw, nodeGPUsRaw] = await Promise.all([
      getSlurmClusterGPUs(),
      getSlurmPerNodeGPUs(),
    ]);

    const allSlurmGPUs = {};
    const perClusterSlurmGPUs = {}; // Similar to perContextGPUs for Kubernetes
    const perNodeSlurmGPUs = {}; // { 'cluster/node_name': { ... } }

    // Process cluster GPUs (similar to Kubernetes context GPUs)
    // clusterGPUsRaw is expected to be like: [ [cluster_name, [ [gpu_name, counts, capacity, available], ... ] ], ... ]
    for (const clusterData of clusterGPUsRaw) {
      const clusterName = clusterData[0];
      const gpusInCluster = clusterData[1];

      for (const gpuRaw of gpusInCluster) {
        const gpuName = gpuRaw[0];
        // gpuRaw[1] is counts (list of requestable quantities), e.g., [1, 2, 4]
        const gpuRequestableQtyPerNode = gpuRaw[1].join(', ');
        const gpuTotal = gpuRaw[2]; // capacity
        const gpuFree = gpuRaw[3]; // available

        // Aggregate for allSlurmGPUs
        if (gpuName in allSlurmGPUs) {
          allSlurmGPUs[gpuName].gpu_total += gpuTotal;
          allSlurmGPUs[gpuName].gpu_free += gpuFree;
        } else {
          allSlurmGPUs[gpuName] = {
            gpu_total: gpuTotal,
            gpu_free: gpuFree,
            gpu_name: gpuName,
          };
        }

        // Store for perClusterSlurmGPUs (similar to perContextGPUs)
        const clusterGpuKey = `${clusterName}#${gpuName}`; // Unique key for cluster-gpu combo
        perClusterSlurmGPUs[clusterGpuKey] = {
          gpu_name: gpuName,
          gpu_requestable_qty_per_node: gpuRequestableQtyPerNode,
          gpu_total: gpuTotal,
          gpu_free: gpuFree,
          cluster: clusterName,
        };
      }
    }

    // Process node GPUs
    // nodeGPUsRaw is expected to be like: [ {node_name, slurm_cluster_name, partition, gpu_type, total_gpus, free_gpus}, ... ]
    for (const node of nodeGPUsRaw) {
      const clusterName = node.slurm_cluster_name || 'default';
      const key = `${clusterName}/${node.node_name}/${node.gpu_type || '-'}`;
      perNodeSlurmGPUs[key] = {
        node_name: node.node_name,
        gpu_name: node.gpu_type || '-', // gpu_type might be null
        gpu_total: node.total_gpus || 0,
        gpu_free: node.free_gpus || 0,
        cluster: clusterName,
        partition: node.partition || 'default', // partition might be null
      };
    }

    return {
      allSlurmGPUs: Object.values(allSlurmGPUs).sort((a, b) =>
        a.gpu_name.localeCompare(b.gpu_name)
      ),
      perClusterSlurmGPUs: Object.values(perClusterSlurmGPUs).sort(
        (a, b) =>
          a.cluster.localeCompare(b.cluster) ||
          a.gpu_name.localeCompare(b.gpu_name)
      ),
      perNodeSlurmGPUs: Object.values(perNodeSlurmGPUs).sort(
        (a, b) =>
          (a.cluster || '').localeCompare(b.cluster || '') ||
          (a.node_name || '').localeCompare(b.node_name || '') ||
          (a.gpu_name || '').localeCompare(b.gpu_name || '')
      ),
    };
  } catch (error) {
    console.error('Error fetching Slurm GPUs:', error);
    return {
      allSlurmGPUs: [],
      perClusterSlurmGPUs: [],
      perNodeSlurmGPUs: [],
    };
  }
}
