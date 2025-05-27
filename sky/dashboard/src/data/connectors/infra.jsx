import {
  ENDPOINT,
  CLOUDS_LIST,
  COMMON_GPUS,
} from '@/data/connectors/constants';

// Importing from the same directory
import { getClusters } from '@/data/connectors/clusters';
import { getManagedJobs } from '@/data/connectors/jobs';

export async function getCloudInfrastructure() {
  try {
    // Use existing data connectors
    const [clustersData, jobsData] = await Promise.all([
      getClusters(),
      getManagedJobs(),
    ]);

    const clusters = clustersData || [];
    const jobs = jobsData?.jobs || [];

    // Get enabled clouds
    let enabledCloudsList = [];
    try {
      const enabledCloudsResponse = await fetch(`${ENDPOINT}/enabled_clouds`, {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json',
        },
      });

      const id =
        enabledCloudsResponse.headers.get('X-Skypilot-Request-ID') ||
        enabledCloudsResponse.headers.get('X-Request-ID');
      const fetchedData = await fetch(`${ENDPOINT}/api/get?request_id=${id}`);
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

    // Convert to array, filter to only enabled clouds, and sort
    const result = Object.values(cloudsData)
      .filter((cloud) => cloud.enabled)
      .sort((a, b) => b.clusters - a.clusters || b.jobs - a.jobs);

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
  const gpus = await getKubernetesGPUs();
  return gpus;
}

async function getKubernetesContextGPUs() {
  try {
    const response = await fetch(
      `${ENDPOINT}/realtime_kubernetes_gpu_availability`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          context: null,
          name_filter: null,
          quantity_filter: null,
        }),
      }
    );

    if (!response.ok) {
      console.error(
        `Error fetching Kubernetes context GPUs (in getKubernetesContextGPUs): ${response.status} ${response.statusText}`
      );
      return [];
    }

    const id =
      response.headers.get('X-Skypilot-Request-ID') ||
      response.headers.get('x-request-id');

    if (!id) {
      console.error(
        'No request ID returned for Kubernetes GPU availability (in getKubernetesContextGPUs)'
      );
      return [];
    }

    const fetchedData = await fetch(`${ENDPOINT}/api/get?request_id=${id}`);
    const rawText = await fetchedData.text();

    if (fetchedData.status === 500) {
      try {
        const errorData = JSON.parse(rawText);
        if (errorData.detail && errorData.detail.error) {
          try {
            const errorDetail = JSON.parse(errorData.detail.error);
            console.error(
              '[infra.jsx] getKubernetesContextGPUs: Server error detail:',
              errorDetail.message
            );
          } catch (jsonError) {
            console.error(
              '[infra.jsx] getKubernetesContextGPUs: Error parsing server error JSON:',
              jsonError,
              'Original error text:',
              errorData.detail.error
            );
          }
        }
      } catch (parseError) {
        console.error(
          '[infra.jsx] getKubernetesContextGPUs: Error parsing 500 error response JSON:',
          parseError,
          'Raw text was:',
          rawText
        );
      }
      return [];
    }
    const data = JSON.parse(rawText);
    const contextGPUs = data.return_value ? JSON.parse(data.return_value) : [];
    return contextGPUs;
  } catch (error) {
    console.error(
      '[infra.jsx] Outer error in getKubernetesContextGPUs:',
      error
    );
    return [];
  }
}

async function getAllContexts() {
  try {
    const response = await fetch(`${ENDPOINT}/all_contexts`, {
      method: 'GET', // Assuming GET for a simple list endpoint
      headers: {
        'Content-Type': 'application/json',
      },
    });
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
    const fetchedData = await fetch(`${ENDPOINT}/api/get?request_id=${id}`);
    const data = await fetchedData.json();
    return data.return_value ? JSON.parse(data.return_value) : [];
  } catch (error) {
    console.error('[infra.jsx] Error in getAllContexts:', error);
    return [];
  }
}

async function getKubernetesPerNodeGPUs(context) {
  try {
    const response = await fetch(`${ENDPOINT}/kubernetes_node_info`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        context: context,
      }),
    });
    const id =
      response.headers.get('X-Skypilot-Request-ID') ||
      response.headers.get('x-request-id');
    const fetchedData = await fetch(`${ENDPOINT}/api/get?request_id=${id}`);
    if (fetchedData.status === 500) {
      try {
        const data = await fetchedData.json();
        if (data.detail && data.detail.error) {
          try {
            const error = JSON.parse(data.detail.error);
            console.error(
              'Error fetching Kubernetes per node GPUs:',
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
    console.error(
      '[infra.jsx] Error in getKubernetesPerNodeGPUs for context',
      context,
      ':',
      error
    );
    return {};
  }
}

async function getKubernetesGPUs() {
  try {
    // 1. Fetch all context names (Kubernetes + SSH)
    const allAvailableContextNames = await getAllContexts();

    if (!allAvailableContextNames || allAvailableContextNames.length === 0) {
      console.log('No contexts found from /all_contexts endpoint.');
      return {
        allContextNames: [],
        allGPUs: [],
        perContextGPUs: [],
        perNodeGPUs: [],
      };
    }

    // 2. Fetch GPU availability information (this might not include all contexts)
    const contextGPUAvailability = await getKubernetesContextGPUs();
    const gpuAvailabilityMap = new Map();
    if (contextGPUAvailability) {
      contextGPUAvailability.forEach((cg) => {
        gpuAvailabilityMap.set(cg[0], cg[1]); // cg[0] is context, cg[1] is gpusInCtx
      });
    }

    const allGPUsSummary = {};
    const perContextGPUsData = {};
    const perNodeGPUs_dict = {};

    // 3. Iterate through all_available_context_names and fetch node info for each
    for (const context of allAvailableContextNames) {
      if (!perContextGPUsData[context]) {
        perContextGPUsData[context] = [];
      }

      // Get GPU details from the availability map if present
      const gpusInCtx = gpuAvailabilityMap.get(context);
      if (gpusInCtx && gpusInCtx.length > 0) {
        for (const gpu of gpusInCtx) {
          const gpuName = gpu[0];
          const gpuRequestableQtyPerNode = gpu[1].join(', ');
          const gpuTotal = gpu[2];
          const gpuFree = gpu[3];

          if (gpuName in allGPUsSummary) {
            allGPUsSummary[gpuName].gpu_total += gpuTotal;
            allGPUsSummary[gpuName].gpu_free += gpuFree;
          } else {
            allGPUsSummary[gpuName] = {
              gpu_total: gpuTotal,
              gpu_free: gpuFree,
              gpu_name: gpuName,
            };
          }

          perContextGPUsData[context].push({
            gpu_name: gpuName,
            gpu_requestable_qty_per_node: gpuRequestableQtyPerNode,
            gpu_total: gpuTotal,
            gpu_free: gpuFree,
            context: context,
          });
        }
      }

      // Fetch node information for the current context
      const nodeInfoForContext = await getKubernetesPerNodeGPUs(context);
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
    };
    return result;
  } catch (error) {
    console.error('[infra.jsx] Outer error in getKubernetesGPUs:', error);
    return {
      allContextNames: [],
      allGPUs: [],
      perContextGPUs: [],
      perNodeGPUs: [],
    };
  }
}

export async function getCloudGPUs() {
  try {
    const response = await fetch(`${ENDPOINT}/list_accelerator_counts`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        clouds: CLOUDS_LIST,
        gpus_only: true,
      }),
    });
    const id =
      response.headers.get('X-Skypilot-Request-ID') ||
      response.headers.get('x-request-id');
    const fetchedData = await fetch(`${ENDPOINT}/api/get?request_id=${id}`);
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

    const response = await fetch(`${ENDPOINT}/list_accelerators`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        gpus_only: true,
        name_filter: gpuName,
        quantity_filter: gpuCount,
        clouds: CLOUDS_LIST,
        case_sensitive: false,
        all_regions: true,
      }),
    });
    const id = response.headers.get('x-request-id');
    const fetchedData = await fetch(`${ENDPOINT}/api/get?request_id=${id}`);

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
