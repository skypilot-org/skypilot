import {
  ENDPOINT,
  CLOUDS_LIST,
  COMMON_GPUS,
} from '@/data/connectors/constants';

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
    const id = response.headers.get('x-request-id');
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
    const id = response.headers.get('x-request-id');
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
    // Get context gpus
    const contextGPUs = await getKubernetesContextGPUs();

    const allGPUs = {};
    const perContextGPUsData = {}; // Renamed to avoid confusion, this will be { context: [gpu1, gpu2] }
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

        // Push each GPU type into the array for the context
        perContextGPUsData[context].push({
          gpu_name: gpuName,
          gpu_requestable_qty_per_node: gpuRequestableQtyPerNode,
          gpu_total: gpuTotal,
          gpu_free: gpuFree,
          context: context,
        });
      }

      // Get per node gpus
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
      // Convert to slice
      allGPUs: Object.values(allGPUs).sort((a, b) =>
        a.gpu_name.localeCompare(b.gpu_name)
      ),
      // Flatten perContextGPUsData for the expected output structure
      // The component gpus.jsx will group it again using useMemo
      perContextGPUs: Object.values(perContextGPUsData)
        .flat()
        .sort(
          (a, b) =>
            a.context.localeCompare(b.context) ||
            a.gpu_name.localeCompare(b.gpu_name)
        ),
      // Sort by context first, and for the same context, sort by node_name, and for the same node_name, sort by gpu_name
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

async function getSlurmPartitionGPUs() {
  try {
    const response = await fetch(`${ENDPOINT}/slurm_gpu_availability`, {
      method: 'POST', // Matches server endpoint
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({}), // Empty body, name_filter/quantity_filter are optional
    });
    const id = response.headers.get('x-request-id');
    const fetchedData = await fetch(`${ENDPOINT}/api/get?request_id=${id}`);
    if (fetchedData.status === 500) {
      try {
        const data = await fetchedData.json();
        if (data.detail && data.detail.error) {
          try {
            const error = JSON.parse(data.detail.error);
            console.error(
              'Error fetching Slurm partition GPUs:',
              error.message
            );
          } catch (jsonError) {
            console.error('Error parsing JSON for Slurm error:', jsonError);
          }
        }
      } catch (parseError) {
        console.error('Error parsing JSON for Slurm 500 response:', parseError);
      }
      return [];
    }
    const data = await fetchedData.json();
    const partitionGPUs = data.return_value
      ? JSON.parse(data.return_value)
      : [];
    return partitionGPUs;
  } catch (error) {
    console.error('Error fetching Slurm partition GPUs:', error);
    return [];
  }
}

async function getSlurmPerNodeGPUs() {
  try {
    // Note: sdk.slurm_node_info() uses GET
    const response = await fetch(`${ENDPOINT}/slurm_node_info`, {
      method: 'GET', // Matches server endpoint
      headers: {
        'Content-Type': 'application/json',
      },
    });
    const id = response.headers.get('x-request-id');
    const fetchedData = await fetch(`${ENDPOINT}/api/get?request_id=${id}`);
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
      return []; // Return empty array for consistency, though cli.py processes it as a list of dicts
    }
    const data = await fetchedData.json();
    // The server directly returns a list of node dicts for slurm_node_info
    const nodeInfo = data.return_value ? JSON.parse(data.return_value) : [];
    return nodeInfo;
  } catch (error) {
    console.error('Error fetching Slurm per node GPUs:', error);
    return [];
  }
}

export async function getSlurmServiceGPUs() {
  try {
    const partitionGPUsRaw = await getSlurmPartitionGPUs();
    const nodeGPUsRaw = await getSlurmPerNodeGPUs();

    const allSlurmGPUs = {};
    const perPartitionSlurmGPUs = {}; // { partition: { gpu_name: ..., ... } }
    const perNodeSlurmGPUs = {}; // { 'partition/node_name': { ... } }

    // Process partition GPUs
    // partitionGPUsRaw is expected to be like: [ [partition_name, [ [gpu_name, counts, capacity, available], ... ] ], ... ]
    for (const partitionData of partitionGPUsRaw) {
      const partitionName = partitionData[0];
      const gpusInPartition = partitionData[1];

      for (const gpuRaw of gpusInPartition) {
        const gpuName = gpuRaw[0];
        // gpuRaw[1] is counts (list of requestable quantities), e.g., [1, 2, 4]
        // For simplicity, we might not need all individual counts in the summary,
        // but it's good to have if detailed view is needed later.
        // For now, let's just store it as a string like k8s.
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

        // Store for perPartitionSlurmGPUs
        // Assuming one dominant GPU type per partition for this simplified structure,
        // or that the data structure implies this. If multiple GPU types can exist
        // meaningfully under a single 'perPartitionSlurmGPUs[partitionName]' entry,
        // this will need adjustment (e.g., make it an array).
        // For now, let's create an entry for each GPU type within a partition.
        const partitionGpuKey = `${partitionName}#${gpuName}`; // Unique key for partition-gpu combo
        perPartitionSlurmGPUs[partitionGpuKey] = {
          gpu_name: gpuName,
          gpu_requestable_qty_per_node: gpuRequestableQtyPerNode,
          gpu_total: gpuTotal,
          gpu_free: gpuFree,
          partition: partitionName,
        };
      }
    }

    // Process node GPUs
    // nodeGPUsRaw is expected to be like: [ {node_name, partition, gpu_type, total_gpus, free_gpus}, ... ]
    for (const node of nodeGPUsRaw) {
      const key = `${node.partition || 'default'}/${node.node_name}/${node.gpu_type || '-'}`;
      perNodeSlurmGPUs[key] = {
        node_name: node.node_name,
        gpu_name: node.gpu_type || '-', // gpu_type might be null
        gpu_total: node.total_gpus || 0,
        gpu_free: node.free_gpus || 0,
        partition: node.partition || 'default', // partition might be null
      };
    }

    return {
      allSlurmGPUs: Object.values(allSlurmGPUs).sort((a, b) =>
        a.gpu_name.localeCompare(b.gpu_name)
      ),
      perPartitionSlurmGPUs: Object.values(perPartitionSlurmGPUs).sort(
        (a, b) =>
          a.partition.localeCompare(b.partition) ||
          a.gpu_name.localeCompare(b.gpu_name)
      ),
      perNodeSlurmGPUs: Object.values(perNodeSlurmGPUs).sort(
        (a, b) =>
          (a.partition || '').localeCompare(b.partition || '') ||
          (a.node_name || '').localeCompare(b.node_name || '') ||
          (a.gpu_name || '').localeCompare(b.gpu_name || '')
      ),
    };
  } catch (error) {
    console.error('Error fetching Slurm GPUs:', error);
    return {
      allSlurmGPUs: [],
      perPartitionSlurmGPUs: [],
      perNodeSlurmGPUs: [],
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
    const id = response.headers.get('x-request-id');
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
    // commonGPUs, keys from COMMON_GPUS in allGPUs and values are the count array join with comma
    const commonGPUs = Object.keys(allGPUs)
      .filter((gpu) => COMMON_GPUS.includes(gpu))
      .map((gpu) => ({
        gpu_name: gpu,
        gpu_quantities: allGPUs[gpu].join(', '),
      }))
      .sort((a, b) => a.gpu_name.localeCompare(b.gpu_name));
    // tpus, keys starts with 'tpu-' in allGPUs and values are the count array join with comma
    const tpus = Object.keys(allGPUs)
      .filter((gpu) => gpu.startsWith('tpu-'))
      .map((gpu) => ({
        gpu_name: gpu,
        gpu_quantities: allGPUs[gpu].join(', '),
      }))
      .sort((a, b) => a.gpu_name.localeCompare(b.gpu_name));
    // otherGPUs, keys not in COMMON_GPUS and not starts with 'tpu-' in allGPUs and values are the count array join with comma
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
