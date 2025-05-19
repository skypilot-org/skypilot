import {
  ENDPOINT,
  CLOUDS_LIST,
  COMMON_GPUS,
} from '@/data/connectors/constants';

export async function getCloudInfrastructure() {
  try {
    const response = await fetch(`${ENDPOINT}/clusters`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({}),
    });
    
    const id = response.headers.get('X-Skypilot-Request-ID') || 
               response.headers.get('X-Request-ID');
    const fetchedData = await fetch(`${ENDPOINT}/api/get?request_id=${id}`);
    const data = await fetchedData.json();
    const clusters = data.return_value ? JSON.parse(data.return_value) : [];
    
    const jobsResponse = await fetch(`${ENDPOINT}/jobs`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      }
    });
    
    const jobsId = jobsResponse.headers.get('X-Skypilot-Request-ID') || 
                   jobsResponse.headers.get('X-Request-ID');
    const jobsFetchedData = await fetch(`${ENDPOINT}/api/get?request_id=${jobsId}`);
    const jobsData = await jobsFetchedData.json();
    const jobs = jobsData.return_value ? JSON.parse(jobsData.return_value).jobs : [];
    
    const cloudsData = {};
    
    CLOUDS_LIST.forEach(cloud => {
      cloudsData[cloud] = {
        name: cloud,
        clusters: 0,
        jobs: 0,
        enabled: false
      };
    });
    
    clusters.forEach(cluster => {
      const cloud = cluster.cloud.toLowerCase();
      if (cloudsData[cloud]) {
        cloudsData[cloud].clusters += 1;
        cloudsData[cloud].enabled = true;
      }
    });
    
    jobs.forEach(job => {
      let cloud = job.cloud;
      
      if (!cloud && job.cluster_resources && job.cluster_resources !== '-') {
        try {
          cloud = job.cluster_resources.split('(')[0].split('x').pop().trim().toLowerCase();
        } catch (error) {
          cloud = null;
        }
      }
      
      if (cloud && cloudsData[cloud]) {
        cloudsData[cloud].jobs += 1;
      }
    });
    
    const result = Object.values(cloudsData)
      .filter(cloud => cloud.enabled)
      .sort((a, b) => b.clusters - a.clusters || b.jobs - a.jobs);
    
    return result;
  } catch (error) {
    console.error('Error fetching cloud infrastructure:', error);
    return [];
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
        body: JSON.stringify({}),
      }
    );
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
              'Error fetching Kubernetes context GPUs:',
              error.message
            );
          } catch (jsonError) {
            console.error('Error parsing JSON:', jsonError);
          }
        }
      } catch (parseError) {
        console.error('Error parsing JSON:', parseError);
      }
      return [];
    }
    const data = await fetchedData.json();
    const contextGPUs = data.return_value ? JSON.parse(data.return_value) : [];
    return contextGPUs;
  } catch (error) {
    console.error('Error fetching Kubernetes context GPUs:', error);
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
    const nodeGPUs = data.return_value ? JSON.parse(data.return_value) : {};
    return nodeGPUs['node_info_dict'] || {};
  } catch (error) {
    console.error('Error fetching Kubernetes per node GPUs:', error);
    return {};
  }
}

async function getKubernetesGPUs() {
  try {
    const contextGPUs = await getKubernetesContextGPUs();

    const allGPUs = {};
    const perContextGPUsData = {};
    const perNodeGPUs = {};

    for (const contextGPU of contextGPUs) {
      const context = contextGPU[0];
      const gpus = contextGPU[1];

      if (!perContextGPUsData[context]) {
        perContextGPUsData[context] = [];
      }

      for (const gpu of gpus) {
        const gpuName = gpu[0];
        const gpuRequestableQtyPerNode = gpu[1].join(', ');
        const gpuTotal = gpu[2];
        const gpuFree = gpu[3];

        if (gpuName in allGPUs) {
          allGPUs[gpuName].gpu_total += gpuTotal;
          allGPUs[gpuName].gpu_free += gpuFree;
        } else {
          allGPUs[gpuName] = {
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

      const nodeGPUs = await getKubernetesPerNodeGPUs(context);
      for (const node in nodeGPUs) {
        perNodeGPUs[`${context}/${node}`] = {
          node_name: nodeGPUs[node]['name'],
          gpu_name: nodeGPUs[node]['accelerator_type'] || '-',
          gpu_total: nodeGPUs[node]['total']['accelerator_count'],
          gpu_free: nodeGPUs[node]['free']['accelerators_available'],
          context: context,
        };
      }
    }
    return {
      allGPUs: Object.values(allGPUs).sort((a, b) =>
        a.gpu_name.localeCompare(b.gpu_name)
      ),
      perContextGPUs: Object.values(perContextGPUsData)
        .flat()
        .sort(
          (a, b) =>
            a.context.localeCompare(b.context) ||
            a.gpu_name.localeCompare(b.gpu_name)
        ),
      perNodeGPUs: Object.values(perNodeGPUs).sort(
        (a, b) =>
          a.context.localeCompare(b.context) ||
          a.node_name.localeCompare(b.node_name) ||
          a.gpu_name.localeCompare(b.gpu_name)
      ),
    };
  } catch (error) {
    console.error('Error fetching Kubernetes GPUs:', error);
    return {
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
