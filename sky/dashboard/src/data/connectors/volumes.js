import { apiClient } from '@/data/connectors/client';

export async function getVolumes() {
  try {
    const data = await apiClient.fetch('/volumes', {}, 'GET');
    // Transform the data to match the expected format
    const transformedData =
      data.map((volume) => {
        // Build infra field from cloud, region, zone
        let infra = volume.cloud || '';
        if (volume.region) {
          infra += `/${volume.region}`;
        }
        if (volume.zone) {
          infra += `/${volume.zone}`;
        }

        return {
          name: volume.name,
          launched_at: volume.launched_at,
          user_hash: volume.user_hash,
          user_name: volume.user_name || '-',
          workspace: volume.workspace || '-',
          last_attached_at: volume.last_attached_at,
          status: volume.status,
          type: volume.type,
          cloud: volume.cloud,
          region: volume.region,
          zone: volume.zone,
          infra: infra,
          size: `${volume.size}Gi`,
          config: volume.config,
          storage_class: volume.config?.storage_class_name || '-',
          access_mode: volume.config?.access_mode || '-',
          namespace: volume.config?.namespace || '-',
          name_on_cloud: volume.name_on_cloud,
          usedby_pods: volume.usedby_pods,
          usedby_clusters: volume.usedby_clusters,
        };
      }) || [];

    return transformedData;
  } catch (error) {
    console.error('Failed to fetch volumes:', error);
    return [];
  }
}

export async function deleteVolume(volumeName) {
  let msg = '';
  try {
    const response = await apiClient.post('/volumes/delete', {
      names: [volumeName],
    });
    const id =
      response.headers.get('X-SkyPilot-Request-ID') ||
      response.headers.get('X-Request-ID');
    const fetchedData = await apiClient.get(`/api/get?request_id=${id}`);
    if (fetchedData.status === 500) {
      try {
        const data = await fetchedData.json();
        if (data.detail && data.detail.error) {
          try {
            const error = JSON.parse(data.detail.error);
            // Handle specific error types
            msg = error.message;
          } catch (jsonError) {
            console.error('Error parsing JSON:', jsonError);
          }
        }
      } catch (parseError) {
        console.error('Error parsing JSON:', parseError);
      }
      return { success: false, msg: msg };
    }
    return { success: true };
  } catch (error) {
    console.error('Failed to delete volume:', error);
    return { success: false, msg: error.message };
  }
}
