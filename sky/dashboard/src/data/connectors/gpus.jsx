import { ENDPOINT } from '@/data/connectors/constants';

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
    const perContextGPUs = {};
    const perNodeGPUs = {};

    for (const contextGPU of contextGPUs) {
      const context = contextGPU[0];
      const gpus = contextGPU[1];
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

        perContextGPUs[context] = {
          gpu_name: gpuName,
          gpu_requestable_qty_per_node: gpuRequestableQtyPerNode,
          gpu_total: gpuTotal,
          gpu_free: gpuFree,
          context: context,
        };
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
      // Sort by context first, and for the same context, sort by gpu_name
      perContextGPUs: Object.values(perContextGPUs).sort(
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
