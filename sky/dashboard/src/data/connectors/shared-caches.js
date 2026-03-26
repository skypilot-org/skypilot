import { apiClient } from '@/data/connectors/client';
import { getErrorMessageFromResponse } from '@/data/utils';

export async function getSharedCaches() {
  try {
    const data = await apiClient.fetch('/shared_caches', {}, 'GET');
    return data || [];
  } catch (error) {
    console.error('Failed to fetch shared caches:', error);
    throw error;
  }
}

export async function upsertSharedCache({ context, spec, cache_paths }) {
  let msg = '';
  try {
    const response = await apiClient.post('/shared_caches/upsert', {
      context: context || null,
      spec,
      cache_paths,
    });
    if (!response.ok) {
      const errorMessage = await getErrorMessageFromResponse(response);
      msg = `Failed to save shared cache: ${errorMessage}`;
      console.error(msg);
      return { success: false, msg };
    }
    const id = response.headers.get('X-SkyPilot-Request-ID');
    if (!id) {
      return { success: false, msg: 'No request ID received from server' };
    }
    const fetchedData = await apiClient.get(`/api/get?request_id=${id}`);
    if (!fetchedData.ok) {
      const errorMessage = await getErrorMessageFromResponse(fetchedData);
      msg = `Failed to save shared cache: ${errorMessage}`;
      console.error(msg);
      return { success: false, msg };
    }
    return { success: true };
  } catch (error) {
    msg = `Failed to save shared cache: ${error}`;
    console.error(msg);
    return { success: false, msg };
  }
}

export async function deleteSharedCache({ context, volume_name }) {
  let msg = '';
  try {
    const response = await apiClient.post('/shared_caches/delete', {
      context: context || null,
      volume_name,
    });
    if (!response.ok) {
      const errorMessage = await getErrorMessageFromResponse(response);
      msg = `Failed to delete shared cache: ${errorMessage}`;
      console.error(msg);
      return { success: false, msg };
    }
    const id = response.headers.get('X-SkyPilot-Request-ID');
    if (!id) {
      return { success: false, msg: 'No request ID received from server' };
    }
    const fetchedData = await apiClient.get(`/api/get?request_id=${id}`);
    if (!fetchedData.ok) {
      const errorMessage = await getErrorMessageFromResponse(fetchedData);
      msg = `Failed to delete shared cache: ${errorMessage}`;
      console.error(msg);
      return { success: false, msg };
    }
    return { success: true };
  } catch (error) {
    msg = `Failed to delete shared cache: ${error}`;
    console.error(msg);
    return { success: false, msg };
  }
}

export async function getK8sContexts() {
  try {
    const data = await apiClient.fetch(
      '/shared_caches/k8s_contexts',
      {},
      'GET'
    );
    return data || [];
  } catch (error) {
    console.error('Failed to fetch K8s contexts:', error);
    return [];
  }
}

export async function getStorageClasses(context) {
  let msg = '';
  try {
    const response = await apiClient.post('/shared_caches/storage_classes', {
      context: context || null,
    });
    if (!response.ok) {
      return [];
    }
    const id = response.headers.get('X-SkyPilot-Request-ID');
    if (!id) {
      return [];
    }
    const fetchedData = await apiClient.get(`/api/get?request_id=${id}`);
    if (!fetchedData.ok) {
      return [];
    }
    const data = await fetchedData.json();
    return data?.return_value ? JSON.parse(data.return_value) : [];
  } catch (error) {
    console.error('Failed to fetch storage classes:', error);
    return [];
  }
}

export async function getRwmVolumes() {
  try {
    const data = await apiClient.fetch('/shared_caches/rwm_volumes', {}, 'GET');
    return data || [];
  } catch (error) {
    console.error('Failed to fetch RWM volumes:', error);
    return [];
  }
}

export async function applyVolume({
  name,
  size,
  storageClass,
  region,
  namespace,
  volumeType,
  hostPath,
  cleanupOnDeletion,
}) {
  const isHostPath = volumeType === 'k8s-hostpath';
  const config = isHostPath
    ? {
        host_path: hostPath,
        cleanup_on_deletion: cleanupOnDeletion ?? true,
      }
    : {
        storage_class_name: storageClass || null,
        access_mode: 'ReadWriteMany',
        namespace: namespace || null,
      };

  let msg = '';
  try {
    const response = await apiClient.post('/volumes/apply', {
      name,
      volume_type: volumeType || 'k8s-pvc',
      cloud: 'kubernetes',
      region: region || null,
      size: isHostPath ? null : size ? String(size) : null,
      config,
      use_existing: false,
    });
    if (!response.ok) {
      const errorMessage = await getErrorMessageFromResponse(response);
      msg = `Failed to create volume: ${errorMessage}`;
      console.error(msg);
      return { success: false, msg };
    }
    const id = response.headers.get('X-SkyPilot-Request-ID');
    if (!id) {
      return { success: false, msg: 'No request ID received from server' };
    }
    const fetchedData = await apiClient.get(`/api/get?request_id=${id}`);
    if (!fetchedData.ok) {
      const errorMessage = await getErrorMessageFromResponse(fetchedData);
      msg = `Failed to create volume: ${errorMessage}`;
      console.error(msg);
      return { success: false, msg };
    }
    return { success: true };
  } catch (error) {
    msg = `Failed to create volume: ${error}`;
    console.error(msg);
    return { success: false, msg };
  }
}
