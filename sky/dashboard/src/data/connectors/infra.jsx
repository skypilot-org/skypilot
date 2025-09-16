import { CLOUDS_LIST, COMMON_GPUS } from '@/data/connectors/constants';

// Importing from the same directory
import { apiClient } from '@/data/connectors/client';

export async function getCloudInfrastructure(forceRefresh = true) {
  const dashboardCache = (await import('@/lib/cache')).default;
  const { getClusters } = await import('@/data/connectors/clusters');
  const { getManagedJobs } = await import('@/data/connectors/jobs');
  try {
    const jobsData = await dashboardCache.get(getManagedJobs, [
      { allUsers: true },
    ]);
    const jobs = jobsData?.jobs || [];
    const clustersData = await dashboardCache.get(getClusters);
    const clusters = clustersData || [];
    // Get enabled clouds
    let enabledCloudsList = [];
    try {
      // If forceRefresh is true, first run sky check to refresh cloud status
      if (forceRefresh) {
        console.log('Force refreshing clouds by running sky check...');
        try {
          const checkResponse = await apiClient.post('/check', {});
          const checkId =
            checkResponse.headers.get('X-Skypilot-Request-ID') ||
            checkResponse.headers.get('X-Request-ID');

          // Wait for the check to complete
          const checkResult = await apiClient.get(
            `/api/get?request_id=${checkId}`
          );
          const checkData = await checkResult.json();
          console.log('Sky check completed:', checkData);
        } catch (checkError) {
          console.error('Error running sky check:', checkError);
          // Continue anyway - we'll still try to get the cached enabled clouds
        }
      }

      const enabledCloudsResponse = await apiClient.get(`/enabled_clouds`);

      const id =
        enabledCloudsResponse.headers.get('X-Skypilot-Request-ID') ||
        enabledCloudsResponse.headers.get('X-Request-ID');
      const fetchedData = await apiClient.get(`/api/get?request_id=${id}`);
      const data = await fetchedData.json();
      enabledCloudsList = data.return_value
        ? JSON.parse(data.return_value)
        : [];
      console.log('Enabled clouds:', enabledCloudsList);
    } catch (error) {
      console.error('Error fetching enabled clouds:', error);
      // If there's an error, we'll use clusters and jobs to determine enabled clouds
      enabledCloudsList = [];
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
    return {
      clouds: [],
      totalClouds: CLOUDS_LIST.length,
      enabledClouds: 0,
    };
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

    // Step 1: Get all accessible workspaces for the user
    const { getWorkspaces } = await import('@/data/connectors/workspaces');
    console.log('[DEBUG] About to call getWorkspaces()');
    const workspacesData = await getWorkspaces();
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
        contextStats: {},
        contextWorkspaceMap: {},
      };
    }

    // Step 2: For each workspace, fetch enabled clouds with expanded infrastructure
    const { getEnabledClouds } = await import('@/data/connectors/workspaces');
    const workspaceInfraData = {};
    const allContextsAcrossWorkspaces = [];
    const contextWorkspaceMap = {};

    for (const [workspaceName, workspaceConfig] of Object.entries(
      workspacesData
    )) {
      console.log(`Fetching infrastructure for workspace: ${workspaceName}`);

      try {
        // Get enabled clouds with expanded infrastructure for this workspace
        console.log(
          `[DEBUG] Fetching enabled clouds for workspace: ${workspaceName}`
        );
        const expandedClouds = await getEnabledClouds(workspaceName, true);
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
              console.log(`[DEBUG] Extracted kubernetes context: ${context}`);
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
              console.log(`[DEBUG] Extracted SSH context: ${sshContextName}`);
              allContextsAcrossWorkspaces.push(sshContextName);
              if (!contextWorkspaceMap[sshContextName]) {
                contextWorkspaceMap[sshContextName] = [];
              }
              if (
                !contextWorkspaceMap[sshContextName].includes(workspaceName)
              ) {
                contextWorkspaceMap[sshContextName].push(workspaceName);
              }
              workspaceInfraData[workspaceName].contexts.push(sshContextName);
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
        workspaceInfraData[workspaceName] = {
          config: workspaceConfig,
          clouds: [],
          contexts: [],
          error: error.message,
        };
      }
    }

    // Step 3: Get detailed GPU information for all contexts
    const { getClusters } = await import('@/data/connectors/clusters');
    const dashboardCache = (await import('@/lib/cache')).default;
    const clustersData = await dashboardCache.get(getClusters);
    const clusters = clustersData || [];

    // Get context stats (cluster counts)
    const contextStats = await getContextClusters(clusters);

    // Get GPU data for all contexts (filter out any undefined contexts)
    const validContexts = allContextsAcrossWorkspaces.filter(
      (context) => context && typeof context === 'string'
    );
    const gpuData = await getKubernetesGPUsFromContexts(validContexts);

    const finalResult = {
      workspaces: workspaceInfraData,
      allContextNames: [...new Set(allContextsAcrossWorkspaces)].sort(),
      allGPUs: gpuData.allGPUs || [],
      perContextGPUs: gpuData.perContextGPUs || [],
      perNodeGPUs: gpuData.perNodeGPUs || [],
      contextStats: contextStats,
      contextWorkspaceMap: contextWorkspaceMap,
    };

    console.log('[DEBUG] Final result:', finalResult);
    console.log('[DEBUG] All contexts found:', allContextsAcrossWorkspaces);
    console.log('[DEBUG] Context workspace map:', contextWorkspaceMap);

    return finalResult;
  } catch (error) {
    console.error('[DEBUG] Failed to fetch workspace infrastructure:', error);
    console.error('[DEBUG] Error stack:', error.stack);
    return {
      workspaces: {},
      allContextNames: [],
      allGPUs: [],
      perContextGPUs: [],
      perNodeGPUs: [],
      contextStats: {},
      contextWorkspaceMap: {},
      error: error.message,
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
      };
    }

    const allGPUsSummary = {};
    const perContextGPUsData = {};
    const perNodeGPUs_dict = {};

    // Get all of the node info for all contexts in parallel and put them
    // in a dictionary keyed by context name.
    const contextNodeInfoList = await Promise.all(
      contextNames.map((context) => getKubernetesPerNodeGPUs(context))
    );
    const contextToNodeInfo = {};
    for (let i = 0; i < contextNames.length; i++) {
      contextToNodeInfo[contextNames[i]] = contextNodeInfoList[i];
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

          const gpuName = nodeData['accelerator_type'];
          const totalCount = nodeData['total']?.['accelerator_count'] || 0;
          const freeCount = nodeData['free']?.['accelerators_available'] || 0;

          if (totalCount > 0) {
            if (!gpuToData[gpuName]) {
              gpuToData[gpuName] = {
                gpu_name: gpuName,
                gpu_requestable_qty_per_node: 0,
                gpu_total: 0,
                gpu_free: 0,
                context: context,
              };
            }
            gpuToData[gpuName].gpu_total += totalCount;
            gpuToData[gpuName].gpu_free += freeCount;
            gpuToData[gpuName].gpu_requestable_qty_per_node = totalCount;
          }
        }
        perContextGPUsData[context] = Object.values(gpuToData);
        for (const gpuName in gpuToData) {
          if (gpuName in allGPUsSummary) {
            allGPUsSummary[gpuName].gpu_total += gpuToData[gpuName].gpu_total;
            allGPUsSummary[gpuName].gpu_free += gpuToData[gpuName].gpu_free;
          } else {
            allGPUsSummary[gpuName] = {
              gpu_total: gpuToData[gpuName].gpu_total,
              gpu_free: gpuToData[gpuName].gpu_free,
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

          perNodeGPUs_dict[`${context}/${nodeName}`] = {
            node_name: nodeData['name'] || nodeName,
            gpu_name: acceleratorType,
            gpu_total: totalAccelerators,
            gpu_free: freeAccelerators,
            ip_address: nodeData['ip_address'] || null,
            context: context,
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
                gpu_name: acceleratorType,
              };
            }
            const existingGpuEntry = perContextGPUsData[context].find(
              (gpu) => gpu.gpu_name === acceleratorType
            );
            if (!existingGpuEntry) {
              perContextGPUsData[context].push({
                gpu_name: acceleratorType,
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

    return {
      allGPUs: Object.values(allGPUsSummary).sort((a, b) =>
        a.gpu_name.localeCompare(b.gpu_name)
      ),
      perContextGPUs: Object.values(perContextGPUsData)
        .flat()
        .sort(
          (a, b) =>
            a.context.localeCompare(b.context) ||
            a.gpu_name.localeCompare(b.gpu_name)
        ),
      perNodeGPUs: Object.values(perNodeGPUs_dict).sort(
        (a, b) =>
          a.context.localeCompare(b.context) ||
          a.node_name.localeCompare(b.node_name) ||
          a.gpu_name.localeCompare(b.gpu_name)
      ),
    };
  } catch (error) {
    console.error('[infra.jsx] Error in getKubernetesGPUsFromContexts:', error);
    return {
      allGPUs: [],
      perContextGPUs: [],
      perNodeGPUs: [],
    };
  }
}

async function getAllContexts() {
  try {
    const response = await apiClient.get(`/all_contexts`);
    if (!response.ok) {
      console.error(
        `Error fetching all contexts: ${response.status} ${response.statusText}`
      );
      return [];
    }
    const id =
      response.headers.get('X-Skypilot-Request-ID') ||
      response.headers.get('x-request-id');
    if (!id) {
      console.error('No request ID returned for /all_contexts');
      return [];
    }
    const fetchedData = await apiClient.get(`/api/get?request_id=${id}`);
    const data = await fetchedData.json();
    return data.return_value ? JSON.parse(data.return_value) : [];
  } catch (error) {
    console.error('[infra.jsx] Error in getAllContexts:', error);
    return [];
  }
}

async function getAllContextsForUser() {
  try {
    const response = await apiClient.get(`/all_contexts_for_user`);
    if (!response.ok) {
      console.error(
        `Error fetching all contexts for user: ${response.status} ${response.statusText}`
      );
      return [];
    }
    const id =
      response.headers.get('X-Skypilot-Request-ID') ||
      response.headers.get('x-request-id');
    if (!id) {
      console.error('No request ID returned for /all_contexts_for_user');
      return [];
    }
    const fetchedData = await apiClient.get(`/api/get?request_id=${id}`);
    const data = await fetchedData.json();
    return data.return_value ? JSON.parse(data.return_value) : [];
  } catch (error) {
    console.error('[infra.jsx] Error in getAllContextsForUser:', error);
    return [];
  }
}

async function getKubernetesPerNodeGPUs(context) {
  try {
    const response = await apiClient.post(`/kubernetes_node_info`, {
      context: context,
    });
    const id =
      response.headers.get('X-Skypilot-Request-ID') ||
      response.headers.get('x-request-id');
    const fetchedData = await apiClient.get(`/api/get?request_id=${id}`);
    if (fetchedData.status === 500) {
      try {
        const data = await fetchedData.json();
        if (data.detail && data.detail.error) {
          try {
            const error = JSON.parse(data.detail.error);
            console.warn(
              `[infra.jsx] Context ${context} unavailable:`,
              error.message
            );
          } catch (jsonError) {
            console.error('Error parsing JSON:', jsonError);
          }
        }
      } catch (parseError) {
        console.error('Error parsing JSON:', parseError);
      }
      return {};
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
    return {};
  }
}

export async function getContextJobs(jobs) {
  try {
    // Count jobs per k8s context/ssh node pool
    const contextStats = {};

    // Process jobs
    jobs.forEach((job) => {
      let contextKey = null;

      // Check if it's a Kubernetes job
      if (job.cloud === 'Kubernetes') {
        // For Kubernetes jobs, the context name is in job.region
        contextKey = job.region;
        if (contextKey) {
          contextKey = `kubernetes/${contextKey}`;
        }
      }
      // Check if it's an SSH Node Pool job
      else if (job.cloud === 'SSH') {
        // For SSH jobs, the node pool name is in job.region
        contextKey = job.region;
        if (contextKey) {
          // Remove 'ssh-' prefix if present for display
          const poolName = contextKey.startsWith('ssh-')
            ? contextKey.substring(4)
            : contextKey;
          contextKey = `ssh/${poolName}`;
        }
      }

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
    return {};
  }
}

export async function getContextClusters(clusters) {
  try {
    // Count clusters per k8s context/ssh node pool
    const contextStats = {};
    clusters.forEach((cluster) => {
      let contextKey = null;

      // Check if it's a Kubernetes cluster
      if (cluster.cloud === 'Kubernetes') {
        // For Kubernetes clusters, the context name is in cluster.region
        contextKey = cluster.region;
        if (contextKey) {
          contextKey = `kubernetes/${contextKey}`;
        }
      }
      // Check if it's an SSH Node Pool cluster
      else if (cluster.cloud === 'SSH') {
        // For SSH clusters, the node pool name is in cluster.region
        contextKey = cluster.region;
        if (contextKey) {
          // Remove 'ssh-' prefix if present for display
          const poolName = contextKey.startsWith('ssh-')
            ? contextKey.substring(4)
            : contextKey;
          contextKey = `ssh/${poolName}`;
        }
      }

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
    return {};
  }
}

async function getKubernetesGPUs(clusters) {
  try {
    // 1. Fetch all context names (Kubernetes + SSH) with workspace information
    const allAvailableContextsWithWorkspaces = await getAllContextsForUser();

    if (
      !allAvailableContextsWithWorkspaces ||
      allAvailableContextsWithWorkspaces.length === 0
    ) {
      console.log('No contexts found from /all_contexts_for_user endpoint.');
      return {
        allContextNames: [],
        allGPUs: [],
        perContextGPUs: [],
        perNodeGPUs: [],
        contextStats: {},
        contextWorkspaceMap: {},
      };
    }

    // Extract unique context names for backward compatibility
    const allAvailableContextNames = [
      ...new Set(allAvailableContextsWithWorkspaces.map((ctx) => ctx.context)),
    ];

    // Create a mapping of context to workspaces
    const contextWorkspaceMap = {};
    allAvailableContextsWithWorkspaces.forEach((ctx) => {
      if (!contextWorkspaceMap[ctx.context]) {
        contextWorkspaceMap[ctx.context] = [];
      }
      if (!contextWorkspaceMap[ctx.context].includes(ctx.workspace)) {
        contextWorkspaceMap[ctx.context].push(ctx.workspace);
      }
    });

    // 2. Fetch cluster counts per context
    const contextStats = await getContextClusters(clusters);

    const allGPUsSummary = {};
    const perContextGPUsData = {};
    const perNodeGPUs_dict = {};

    // Get all of the node info for all contexts in parallel and put them
    // in a dictionary keyed by context name.
    const contextNodeInfoList = await Promise.all(
      allAvailableContextNames.map((context) =>
        getKubernetesPerNodeGPUs(context)
      )
    );
    const contextToNodeInfo = {};
    for (let i = 0; i < allAvailableContextNames.length; i++) {
      contextToNodeInfo[allAvailableContextNames[i]] = contextNodeInfoList[i];
    }

    // 3: Populate the gpuToData map for each context.
    for (const context of allAvailableContextNames) {
      const nodeInfoForContext = contextToNodeInfo[context] || {};
      if (nodeInfoForContext && Object.keys(nodeInfoForContext).length > 0) {
        const gpuToData = {};
        for (const nodeName in nodeInfoForContext) {
          const nodeData = nodeInfoForContext[nodeName];
          const gpuName = nodeData['accelerator_type'];
          const totalCount = nodeData['total']['accelerator_count'];
          const freeCount = nodeData['free']['accelerators_available'];
          if (totalCount > 0) {
            if (!gpuToData[gpuName]) {
              gpuToData[gpuName] = {
                gpu_name: gpuName,
                gpu_requestable_qty_per_node: 0,
                gpu_total: 0,
                gpu_free: 0,
                context: context,
              };
            }
            gpuToData[gpuName].gpu_total += totalCount;
            gpuToData[gpuName].gpu_free += freeCount;
            gpuToData[gpuName].gpu_requestable_qty_per_node = totalCount;
          }
        }
        perContextGPUsData[context] = Object.values(gpuToData);
        for (const gpuName in gpuToData) {
          if (gpuName in allGPUsSummary) {
            allGPUsSummary[gpuName].gpu_total += gpuToData[gpuName].gpu_total;
            allGPUsSummary[gpuName].gpu_free += gpuToData[gpuName].gpu_free;
          } else {
            allGPUsSummary[gpuName] = {
              gpu_total: gpuToData[gpuName].gpu_total,
              gpu_free: gpuToData[gpuName].gpu_free,
              gpu_name: gpuName,
            };
          }
        }
      }
    }

    // 4: Populate the perNodeGPUs_dict map for each context.
    for (const context of allAvailableContextNames) {
      const nodeInfoForContext = contextToNodeInfo[context];
      if (nodeInfoForContext && Object.keys(nodeInfoForContext).length > 0) {
        for (const nodeName in nodeInfoForContext) {
          const nodeData = nodeInfoForContext[nodeName];
          // Ensure accelerator_type, total, and free fields exist or provide defaults
          const acceleratorType = nodeData['accelerator_type'] || '-';
          const totalAccelerators =
            nodeData['total']?.['accelerator_count'] ?? 0;
          const freeAccelerators =
            nodeData['free']?.['accelerators_available'] ?? 0;

          perNodeGPUs_dict[`${context}/${nodeName}`] = {
            node_name: nodeData['name'],
            gpu_name: acceleratorType,
            gpu_total: totalAccelerators,
            gpu_free: freeAccelerators,
            ip_address: nodeData['ip_address'] || null,
            context: context,
          };

          // If this node provides a GPU type not found via GPU availability,
          // add it to perContextGPUsData with 0/0 counts if it's not already there.
          // This helps list CPU-only nodes or nodes with GPUs not picked by availability check.
          if (
            acceleratorType !== '-' &&
            !perContextGPUsData[context].some(
              (gpu) => gpu.gpu_name === acceleratorType
            )
          ) {
            if (!(acceleratorType in allGPUsSummary)) {
              allGPUsSummary[acceleratorType] = {
                gpu_total: 0, // Initialize with 0, will be summed up if multiple nodes have this
                gpu_free: 0,
                gpu_name: acceleratorType,
              };
            }
            // This ensures the GPU type is listed under the context, even if availability check missed it.
            // We can't reliably sum total/free here from nodeInfo alone for per-context summary
            // if the GPU availability check is the source of truth for those numbers.
            // However, we must ensure the accelerator type is listed.
            const existingGpuEntry = perContextGPUsData[context].find(
              (gpu) => gpu.gpu_name === acceleratorType
            );
            if (!existingGpuEntry) {
              perContextGPUsData[context].push({
                gpu_name: acceleratorType,
                gpu_requestable_qty_per_node: '-', // Or derive if possible
                gpu_total: 0, // Placeholder, actual totals come from availability
                gpu_free: 0, // Placeholder
                context: context,
              });
            }
          }
        }
      }
      // If after processing nodes and GPU availability, a context has no GPUs listed
      // but nodes were found, ensure it appears in perContext data (e.g. for CPU only nodes)
      if (
        perContextGPUsData[context].length === 0 &&
        nodeInfoForContext &&
        Object.keys(nodeInfoForContext).length > 0
      ) {
        // This indicates a CPU-only context or one where GPU detection failed in availability check
        // but nodes are present. It's already handled by allAvailableContextNames.
        // We might add a placeholder if needed for UI consistency, but `allContextNames` should list it.
      }
    }

    const result = {
      allContextNames: allAvailableContextNames.sort(),
      allGPUs: Object.values(allGPUsSummary).sort((a, b) =>
        a.gpu_name.localeCompare(b.gpu_name)
      ),
      perContextGPUs: Object.values(perContextGPUsData)
        .flat()
        .sort(
          (a, b) =>
            a.context.localeCompare(b.context) ||
            a.gpu_name.localeCompare(b.gpu_name)
        ),
      perNodeGPUs: Object.values(perNodeGPUs_dict).sort(
        (a, b) =>
          a.context.localeCompare(b.context) ||
          a.node_name.localeCompare(b.node_name) ||
          a.gpu_name.localeCompare(b.gpu_name)
      ),
      contextStats: contextStats,
      contextWorkspaceMap: contextWorkspaceMap,
    };
    return result;
  } catch (error) {
    console.error('[infra.jsx] Outer error in getKubernetesGPUs:', error);
    return {
      allContextNames: [],
      allGPUs: [],
      perContextGPUs: [],
      perNodeGPUs: [],
      contextStats: {},
      contextWorkspaceMap: {},
    };
  }
}

export async function getCloudGPUs() {
  try {
    const response = await apiClient.post(`/list_accelerator_counts`, {
      clouds: CLOUDS_LIST,
      gpus_only: true,
    });
    const id =
      response.headers.get('X-Skypilot-Request-ID') ||
      response.headers.get('x-request-id');
    const fetchedData = await apiClient.get(`/api/get?request_id=${id}`);
    if (fetchedData.status === 500) {
      try {
        const data = await fetchedData.json();
        if (data.detail && data.detail.error) {
          try {
            const error = JSON.parse(data.detail.error);
            console.error('Error fetching cloud GPUs:', error.message);
          } catch (jsonError) {
            console.error('Error parsing JSON:', jsonError);
          }
        }
      } catch (parseError) {
        console.error('Error parsing JSON:', parseError);
      }
      return {
        commonGPUs: [],
        tpus: [],
        otherGPUs: [],
      };
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
    return {
      commonGPUs: [],
      tpus: [],
      otherGPUs: [],
    };
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
    const id =
      response.headers.get('X-Skypilot-Request-ID') ||
      response.headers.get('X-Request-ID');
    const fetchedData = await apiClient.get(`/api/get?request_id=${id}`);

    if (fetchedData.status === 500) {
      console.error('Error fetching detailed GPU info: Server error');
      return [];
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
      return [];
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
    return [];
  }
}
