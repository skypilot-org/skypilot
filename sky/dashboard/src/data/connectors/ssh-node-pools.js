import { ENDPOINT } from '@/data/connectors/constants';

export async function getSSHNodePools() {
  try {
    const response = await fetch(`${ENDPOINT}/ssh_node_pools`, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
      },
    });

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    return await response.json();
  } catch (error) {
    console.error('Error fetching SSH Node Pools:', error);
    return {};
  }
}

export async function updateSSHNodePools(poolsConfig) {
  try {
    const response = await fetch(`${ENDPOINT}/ssh_node_pools`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(poolsConfig),
    });

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    return await response.json();
  } catch (error) {
    console.error('Error updating SSH Node Pools:', error);
    throw error;
  }
}

export async function deleteSSHNodePool(poolName) {
  try {
    const response = await fetch(`${ENDPOINT}/ssh_node_pools/${poolName}`, {
      method: 'DELETE',
      headers: {
        'Content-Type': 'application/json',
      },
    });

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    return await response.json();
  } catch (error) {
    console.error('Error deleting SSH Node Pool:', error);
    throw error;
  }
}

export async function uploadSSHKey(keyName, keyFile) {
  try {
    const formData = new FormData();
    formData.append('key_name', keyName);
    formData.append('key_file', keyFile);

    const response = await fetch(`${ENDPOINT}/ssh_node_pools/keys`, {
      method: 'POST',
      body: formData,
    });

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    return await response.json();
  } catch (error) {
    console.error('Error uploading SSH key:', error);
    throw error;
  }
}

export async function listSSHKeys() {
  try {
    const response = await fetch(`${ENDPOINT}/ssh_node_pools/keys`, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
      },
    });

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    return await response.json();
  } catch (error) {
    console.error('Error listing SSH keys:', error);
    return [];
  }
}

export async function deploySSHNodePool(poolName) {
  try {
    const response = await fetch(`${ENDPOINT}/ssh_node_pools/${poolName}/deploy`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
    });

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    return await response.json();
  } catch (error) {
    console.error('Error deploying SSH Node Pool:', error);
    throw error;
  }
}

export async function sshDownNodePool(poolName) {
  try {
    const response = await fetch(`${ENDPOINT}/ssh_down`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        infra: poolName,
        cleanup: true,
      }),
    });

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    return await response.json();
  } catch (error) {
    console.error('Error calling ssh_down for SSH Node Pool:', error);
    throw error;
  }
}

export async function getSSHNodePoolStatus(poolName) {
  try {
    const response = await fetch(`${ENDPOINT}/ssh_node_pools/${poolName}/status`, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
      },
    });

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    return await response.json();
  } catch (error) {
    console.error('Error fetching SSH Node Pool status:', error);
    throw error;
  }
} 