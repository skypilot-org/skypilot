import { ENDPOINT } from '@/data/connectors/constants';
import { showToast } from '@/data/connectors/toast';
import AuthManager from '@/lib/auth';
import { getAuthHeaders, getContentTypeAuthHeaders } from './client';

// Configuration
const DEFAULT_TAIL_LINES = 1000;

export async function getSSHNodePools() {
  try {
    const headers = getContentTypeAuthHeaders();
    const response = await fetch(`${ENDPOINT}/ssh_node_pools`, {
      method: 'GET',
      headers,
    });

    if (response.status === 401) {
      AuthManager.logout();
      return response;
    }

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
    const headers = getContentTypeAuthHeaders();
    const response = await fetch(`${ENDPOINT}/ssh_node_pools`, {
      method: 'POST',
      headers,
      body: JSON.stringify(poolsConfig),
    });

    if (response.status === 401) {
      AuthManager.logout();
      return response;
    }

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
    const headers = getContentTypeAuthHeaders();
    const response = await fetch(`${ENDPOINT}/ssh_node_pools/${poolName}`, {
      method: 'DELETE',
      headers,
    });

    if (response.status === 401) {
      AuthManager.logout();
      return response;
    }

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

    const headers = getAuthHeaders();

    const response = await fetch(`${ENDPOINT}/ssh_node_pools/keys`, {
      method: 'POST',
      headers,
      body: formData,
    });

    if (response.status === 401) {
      AuthManager.logout();
      return response;
    }

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
    const headers = getContentTypeAuthHeaders();
    const response = await fetch(`${ENDPOINT}/ssh_node_pools/keys`, {
      method: 'GET',
      headers,
    });

    if (response.status === 401) {
      AuthManager.logout();
      return response;
    }

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
    const token = AuthManager.getToken();
    const headers = {
      'Content-Type': 'application/json',
    };
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }
    const response = await fetch(
      `${ENDPOINT}/ssh_node_pools/${poolName}/deploy`,
      {
        method: 'POST',
        headers,
      }
    );

    if (response.status === 401) {
      AuthManager.logout();
      return response;
    }

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
    const headers = getContentTypeAuthHeaders();
    const response = await fetch(
      `${ENDPOINT}/ssh_node_pools/${poolName}/down`,
      {
        method: 'POST',
        headers,
      }
    );

    if (response.status === 401) {
      AuthManager.logout();
      return response;
    }

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    return await response.json();
  } catch (error) {
    console.error('Error tearing down SSH Node Pool:', error);
    throw error;
  }
}

export async function getSSHNodePoolStatus(poolName) {
  try {
    const headers = getContentTypeAuthHeaders();
    const response = await fetch(
      `${ENDPOINT}/ssh_node_pools/${poolName}/status`,
      {
        method: 'GET',
        headers,
      }
    );

    if (response.status === 401) {
      AuthManager.logout();
      return response;
    }

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    return await response.json();
  } catch (error) {
    console.error('Error fetching SSH Node Pool status:', error);
    throw error;
  }
}

export async function streamSSHDeploymentLogs({ requestId, signal, onNewLog }) {
  // Measure timeout from last received data, not from start of request.
  const inactivityTimeout = 300000; // 5 minutes of no data activity
  let lastActivity = Date.now();
  let timeoutId;

  // Create an activity-based timeout promise
  const createTimeoutPromise = () => {
    return new Promise((resolve) => {
      const checkActivity = () => {
        const timeSinceLastActivity = Date.now() - lastActivity;

        if (timeSinceLastActivity >= inactivityTimeout) {
          resolve({ timeout: true });
        } else {
          // Check again after remaining time
          timeoutId = setTimeout(
            checkActivity,
            inactivityTimeout - timeSinceLastActivity
          );
        }
      };

      timeoutId = setTimeout(checkActivity, inactivityTimeout);
    });
  };

  const timeoutPromise = createTimeoutPromise();

  // Create the fetch promise
  const fetchPromise = (async () => {
    try {
      const headers = getContentTypeAuthHeaders();
      const response = await fetch(
        `${ENDPOINT}/api/stream?request_id=${requestId}&format=plain&tail=${DEFAULT_TAIL_LINES}&follow=true`,
        {
          method: 'GET',
          headers,
          // Only use the signal if it's provided
          ...(signal ? { signal } : {}),
        }
      );

      if (response.status === 401) {
        AuthManager.logout();
        return response;
      }

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      // Stream the logs
      const reader = response.body.getReader();

      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          // Update activity timestamp when we receive data
          lastActivity = Date.now();

          const chunk = new TextDecoder().decode(value);
          onNewLog(chunk);
        }
      } finally {
        reader.cancel();
        // Clear the timeout when streaming completes successfully
        if (timeoutId) {
          clearTimeout(timeoutId);
        }
      }
      return { timeout: false };
    } catch (error) {
      // Clear timeout on any error
      if (timeoutId) {
        clearTimeout(timeoutId);
      }

      // If this was an abort, just return silently
      if (error.name === 'AbortError') {
        return { timeout: false };
      }
      throw error;
    }
  })();

  // Race the fetch against the activity-based timeout
  const result = await Promise.race([fetchPromise, timeoutPromise]);

  // Clear any remaining timeout
  if (timeoutId) {
    clearTimeout(timeoutId);
  }

  // If we timed out due to inactivity, show a more informative message
  if (result.timeout) {
    showToast(
      `SSH deployment log stream timed out after ${inactivityTimeout / 1000}s of inactivity`,
      'warning'
    );
    return;
  }
}

// Reusable function for streaming SSH operation logs (deploy or down)
export async function streamSSHOperationLogs({
  requestId,
  signal,
  onNewLog,
  operationType = 'operation',
}) {
  // Measure timeout from last received data, not from start of request.
  const inactivityTimeout = 300000; // 5 minutes of no data activity
  let lastActivity = Date.now();
  let timeoutId;

  // Create an activity-based timeout promise
  const createTimeoutPromise = () => {
    return new Promise((resolve) => {
      const checkActivity = () => {
        const timeSinceLastActivity = Date.now() - lastActivity;

        if (timeSinceLastActivity >= inactivityTimeout) {
          resolve({ timeout: true });
        } else {
          // Check again after remaining time
          timeoutId = setTimeout(
            checkActivity,
            inactivityTimeout - timeSinceLastActivity
          );
        }
      };

      timeoutId = setTimeout(checkActivity, inactivityTimeout);
    });
  };

  const timeoutPromise = createTimeoutPromise();

  // Create the fetch promise
  const fetchPromise = (async () => {
    try {
      const headers = getContentTypeAuthHeaders();
      const response = await fetch(
        `${ENDPOINT}/api/stream?request_id=${requestId}&format=plain&tail=${DEFAULT_TAIL_LINES}&follow=true`,
        {
          method: 'GET',
          headers,
          // Only use the signal if it's provided
          ...(signal ? { signal } : {}),
        }
      );

      if (response.status === 401) {
        AuthManager.logout();
        return response;
      }

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      // Stream the logs
      const reader = response.body.getReader();

      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          // Update activity timestamp when we receive data
          lastActivity = Date.now();

          const chunk = new TextDecoder().decode(value);
          onNewLog(chunk);
        }
      } finally {
        reader.cancel();
        // Clear the timeout when streaming completes successfully
        if (timeoutId) {
          clearTimeout(timeoutId);
        }
      }
      return { timeout: false };
    } catch (error) {
      // Clear timeout on any error
      if (timeoutId) {
        clearTimeout(timeoutId);
      }

      // If this was an abort, just return silently
      if (error.name === 'AbortError') {
        return { timeout: false };
      }
      throw error;
    }
  })();

  // Race the fetch against the activity-based timeout
  const result = await Promise.race([fetchPromise, timeoutPromise]);

  // Clear any remaining timeout
  if (timeoutId) {
    clearTimeout(timeoutId);
  }

  // If we timed out due to inactivity, show a more informative message
  if (result.timeout) {
    showToast(
      `SSH ${operationType} log stream timed out after ${inactivityTimeout / 1000}s of inactivity`,
      'warning'
    );
    return;
  }
}
