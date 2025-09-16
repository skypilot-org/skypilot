import { apiClient } from '@/data/connectors/client';

export async function getWorkspaces() {
  try {
    // Step 1: Call the /workspaces endpoint to schedule the task
    const scheduleResponse = await apiClient.get(`/workspaces`);
    if (!scheduleResponse.ok) {
      throw new Error(
        `Error scheduling getWorkspaces: ${scheduleResponse.statusText} (status ${scheduleResponse.status})`
      );
    }

    // Step 2: Get the request_id from the response headers or body
    let requestId = scheduleResponse.headers.get('X-Skypilot-Request-ID');

    if (!requestId) {
      console.warn(
        'X-Skypilot-Request-ID header not found in /workspaces response. Attempting to find request_id in response body as a fallback.'
      );
      try {
        // .json() consumes the response body. If this fails or doesn't find requestId,
        // we can't try reading the body again for other purposes.
        const scheduleData = await scheduleResponse.json();
        if (scheduleData && scheduleData.request_id) {
          requestId = scheduleData.request_id; // Correctly assign to requestId
          console.log(
            'Found request_id in /workspaces response body (fallback):',
            requestId
          );
        } else {
          // If not in header AND not in body after attempting fallback.
          throw new Error(
            'X-Skypilot-Request-ID header not found AND request_id not found in parsed response body from /workspaces.'
          );
        }
      } catch (e) {
        // This catch handles errors from scheduleResponse.json() or the explicit throw above.
        const errorMessage =
          e.message ||
          'Error processing fallback for request_id from /workspaces response body.';
        console.error(
          'Error in /workspaces request_id fallback logic:',
          errorMessage
        );
        throw new Error(
          `X-Skypilot-Request-ID header not found, and fallback to read request_id from body failed: ${errorMessage}`
        );
      }
    }

    // Step 2.5: Validate that we have a requestId before proceeding
    if (!requestId) {
      // This error indicates a critical failure in obtaining the request_id.
      throw new Error(
        'Failed to obtain X-Skypilot-Request-ID from /workspaces response (checked header and attempted body fallback, but ID is still missing).'
      );
    }

    // Step 3: Poll the /api/get endpoint with the request_id.
    console.log(`Fetching workspace data with request_id: ${requestId}`);
    const resultResponse = await apiClient.get(
      `/api/get?request_id=${requestId}`
    );
    if (!resultResponse.ok) {
      let errorDetail = `Error fetching workspace data for request ID ${requestId}: ${resultResponse.statusText} (status ${resultResponse.status})`;
      try {
        const errorData = await resultResponse.json();
        if (errorData && errorData.detail) {
          let innerDetail = errorData.detail;
          try {
            const parsedDetail = JSON.parse(innerDetail);
            if (parsedDetail && parsedDetail.error) {
              innerDetail = parsedDetail.error;
            } else if (
              parsedDetail &&
              parsedDetail.result &&
              parsedDetail.result.error
            ) {
              innerDetail = parsedDetail.result.error;
            }
          } catch (parseErr) {
            /* Inner detail is not JSON, use as is */
          }
          errorDetail = `Error fetching workspace data for request ID ${requestId}: ${innerDetail}`;
        }
      } catch (e) {
        /* ignore error parsing errorData */
      }
      throw new Error(errorDetail);
    }

    const resultData = await resultResponse.json();

    // Log the entire resultData object to inspect its structure
    console.log('[Connector Debug] Full resultData from /api/get:', resultData);

    // Step 4: Check task status and return the actual workspace data
    if (resultData.status === 'FAILED') {
      let errorMessage = `Unknown error during task execution`;

      // Try to extract error message from various possible locations
      if (resultData.error) {
        if (typeof resultData.error === 'string') {
          errorMessage = resultData.error;
        } else if (typeof resultData.error === 'object') {
          // Handle case where error is an object
          errorMessage =
            resultData.error.message ||
            resultData.error.detail ||
            JSON.stringify(resultData.error);
        }
      } else if (resultData.result && resultData.result.error) {
        if (typeof resultData.result.error === 'string') {
          errorMessage = resultData.result.error;
        } else if (typeof resultData.result.error === 'object') {
          // Handle case where error is an object
          errorMessage =
            resultData.result.error.message ||
            resultData.result.error.detail ||
            JSON.stringify(resultData.result.error);
        }
      } else if (resultData.return_value) {
        // Sometimes the error might be in return_value as a JSON string
        try {
          const parsed = JSON.parse(resultData.return_value);
          if (parsed.error) {
            errorMessage =
              typeof parsed.error === 'string'
                ? parsed.error
                : parsed.error.message ||
                  parsed.error.detail ||
                  JSON.stringify(parsed.error);
          }
        } catch (parseErr) {
          // If return_value is not JSON, use it as is if it looks like an error
          if (
            resultData.return_value.includes('Error') ||
            resultData.return_value.includes('Cannot')
          ) {
            errorMessage = resultData.return_value;
          }
        }
      }

      throw new Error(errorMessage);
    }

    // The server.py api_get returns the result in `return_value` for SUCCEEDED tasks.
    // The `return_value` is a JSON string that needs to be parsed.
    let workspaceData = {};
    if (resultData.status === 'SUCCEEDED' && resultData.return_value) {
      try {
        workspaceData = JSON.parse(resultData.return_value);
        console.log(
          'Successfully parsed workspace data from return_value:',
          workspaceData
        );
      } catch (parseError) {
        console.error(
          'Failed to parse workspace data from return_value:',
          parseError,
          'Raw return_value:',
          resultData.return_value
        );
        throw new Error(
          `Failed to parse workspace data for request ID ${requestId}: ${parseError.message}`
        );
      }
    } else if (resultData.result) {
      // Fallback or handling for other successful statuses if they use .result (though SUCCEEDED uses return_value)
      console.warn(
        `Using resultData.result as fallback for status ${resultData.status}`
      );
      workspaceData = resultData.result;
    }

    // This log is kept for consistency but workspaceData is the more accurate variable now
    console.log(
      'Effectively fetched workspace data (to be returned):',
      workspaceData
    );
    return workspaceData || {}; // Return the parsed data or an empty object
  } catch (error) {
    console.error(
      'Failed to fetch workspaces (in getWorkspaces function):',
      error.message,
      error.stack
    );
    throw error; // Re-throw to allow UI to handle it
  }
}

export async function getEnabledClouds(workspaceName = null, expand = false) {
  try {
    // Step 1: Call the /enabled_clouds endpoint to schedule the task
    let url = `/enabled_clouds`;
    const params = new URLSearchParams();
    if (workspaceName) {
      params.append('workspace', workspaceName);
    }
    if (expand) {
      params.append('expand', 'true');
    }
    if (params.toString()) {
      url += `?${params.toString()}`;
    }

    const scheduleResponse = await apiClient.get(url);

    if (!scheduleResponse.ok) {
      throw new Error(
        `Error scheduling getEnabledClouds: ${scheduleResponse.statusText} (status ${scheduleResponse.status})`
      );
    }

    // Step 2: Get the request_id from the response headers or body
    let requestId = scheduleResponse.headers.get('X-Skypilot-Request-ID');

    if (!requestId) {
      console.warn(
        'X-Skypilot-Request-ID header not found in /enabled_clouds response. Attempting to find request_id in response body as a fallback.'
      );
      try {
        const scheduleData = await scheduleResponse.json();
        if (scheduleData && scheduleData.request_id) {
          requestId = scheduleData.request_id;
          console.log(
            'Found request_id in /enabled_clouds response body (fallback):',
            requestId
          );
        } else {
          throw new Error(
            'X-Skypilot-Request-ID header not found AND request_id not found in parsed response body from /enabled_clouds.'
          );
        }
      } catch (e) {
        const errorMessage =
          e.message ||
          'Error processing fallback for request_id from /enabled_clouds response body.';
        console.error(
          'Error in /enabled_clouds request_id fallback logic:',
          errorMessage
        );
        throw new Error(
          `X-Skypilot-Request-ID header not found, and fallback to read request_id from body failed: ${errorMessage}`
        );
      }
    }

    if (!requestId) {
      throw new Error(
        'Failed to obtain X-Skypilot-Request-ID from /enabled_clouds response (checked header and attempted body fallback, but ID is still missing).'
      );
    }

    // Step 3: Poll the /api/get endpoint with the request_id.
    console.log(`Fetching enabled_clouds data with request_id: ${requestId}`);
    const resultResponse = await apiClient.get(
      `/api/get?request_id=${requestId}`
    );
    if (!resultResponse.ok) {
      let errorDetail = `Error fetching enabled_clouds data for request ID ${requestId}: ${resultResponse.statusText} (status ${resultResponse.status})`;
      try {
        const errorData = await resultResponse.json();
        // Similar error detail extraction as in getWorkspaces
        if (errorData && errorData.detail) {
          let innerDetail = errorData.detail;
          // attempt to parse further for more specific error
          try {
            const parsedDetail = JSON.parse(innerDetail);
            if (parsedDetail && parsedDetail.error) {
              innerDetail = parsedDetail.error;
            } else if (
              parsedDetail &&
              parsedDetail.result &&
              parsedDetail.result.error
            ) {
              innerDetail = parsedDetail.result.error;
            }
          } catch (parseErr) {
            /* Inner detail is not JSON, use as is */
          }
          errorDetail = `Error fetching enabled_clouds data for request ID ${requestId}: ${innerDetail}`;
        }
      } catch (e) {
        /* ignore error parsing errorData */
      }
      throw new Error(errorDetail);
    }

    const resultData = await resultResponse.json();
    console.log(
      '[Connector Debug] Full resultData from /api/get for enabled_clouds:',
      resultData
    );

    if (resultData.status === 'FAILED') {
      const errorMessage =
        resultData.error ||
        (resultData.result && resultData.result.error) ||
        'Unknown error during task execution for enabled_clouds';
      throw new Error(
        `Fetching enabled_clouds data failed for request ID ${requestId}: ${errorMessage}`
      );
    }

    let enabledCloudsData = []; // Default to empty array
    if (resultData.status === 'SUCCEEDED' && resultData.return_value) {
      try {
        // enabled_clouds endpoint returns a list of strings directly in return_value,
        // which is also a JSON string.
        enabledCloudsData = JSON.parse(resultData.return_value);
        console.log(
          `Successfully parsed enabled_clouds data for workspace ${workspaceName}:`,
          enabledCloudsData
        );
      } catch (parseError) {
        console.error(
          'Failed to parse enabled_clouds data from return_value:',
          parseError,
          'Raw return_value:',
          resultData.return_value
        );
        throw new Error(
          `Failed to parse enabled_clouds data for request ID ${requestId}: ${parseError.message}`
        );
      }
    } else if (resultData.result) {
      console.warn(
        `Using resultData.result as fallback for enabled_clouds status ${resultData.status}`
      );
      // Assuming result might contain the data directly if not in return_value
      enabledCloudsData = resultData.result;
    }

    // Ensure it returns an array
    return Array.isArray(enabledCloudsData) ? enabledCloudsData : [];
  } catch (error) {
    console.error(
      'Failed to fetch enabled_clouds (in getEnabledClouds function):',
      error.message,
      error.stack
    );
    throw error; // Re-throw to allow UI to handle it
  }
}

// Helper function to poll for task completion
async function pollForTaskCompletion(requestId, taskName) {
  console.log(
    `Polling for ${taskName} task completion with request_id: ${requestId}`
  );

  const resultResponse = await apiClient.get(
    `/api/get?request_id=${requestId}`
  );

  if (!resultResponse.ok) {
    let errorDetail = `Error fetching ${taskName} data for request ID ${requestId}: ${resultResponse.statusText} (status ${resultResponse.status})`;
    try {
      const errorData = await resultResponse.json();
      console.error(
        `[Error Debug] ${taskName} HTTP error response:`,
        JSON.stringify(errorData, null, 2)
      );

      if (errorData && errorData.detail) {
        // When the request fails, the server returns a 500 error with the entire
        // encoded request object in the detail field. The actual error message
        // is nested within this structure.
        if (typeof errorData.detail === 'object' && errorData.detail.error) {
          // The error field contains a JSON string with the actual error details
          try {
            const errorInfo = JSON.parse(errorData.detail.error);
            if (errorInfo && errorInfo.message) {
              errorDetail = `${taskName} failed: ${errorInfo.message}`;
            } else if (errorInfo && typeof errorInfo === 'object') {
              // If no message field, try to extract any meaningful error text
              const errorText = errorInfo.type || JSON.stringify(errorInfo);
              errorDetail = `${taskName} failed: ${errorText}`;
            }
          } catch (parseErr) {
            // If error field is not JSON, use it directly
            errorDetail = `${taskName} failed: ${errorData.detail.error}`;
          }
        } else if (typeof errorData.detail === 'string') {
          // Sometimes the detail might be a string
          errorDetail = `${taskName} failed: ${errorData.detail}`;
        } else {
          // Fallback: try to find any error-like content in the detail object
          const searchForError = (obj, path = '') => {
            if (
              typeof obj === 'string' &&
              (obj.includes('Cannot') ||
                obj.includes('Error') ||
                obj.includes('Failed'))
            ) {
              return obj;
            }
            if (typeof obj === 'object' && obj !== null) {
              for (const [key, value] of Object.entries(obj)) {
                const found = searchForError(
                  value,
                  path ? `${path}.${key}` : key
                );
                if (found) return found;
              }
            }
            return null;
          };

          const foundError = searchForError(errorData.detail);
          if (foundError) {
            errorDetail = `${taskName} failed: ${foundError}`;
          }
        }
      }
    } catch (e) {
      console.error(`[Error Debug] Failed to parse error response:`, e);
      /* ignore error parsing errorData */
    }
    throw new Error(errorDetail);
  }

  const resultData = await resultResponse.json();
  console.log(`[Connector Debug] ${taskName} resultData:`, resultData);

  if (resultData.status === 'FAILED') {
    // Add detailed logging for debugging error structure
    console.error(
      `[Error Debug] ${taskName} failed. Full resultData:`,
      JSON.stringify(resultData, null, 2)
    );
    console.error(`[Error Debug] resultData.error:`, resultData.error);
    console.error(`[Error Debug] resultData.result:`, resultData.result);
    console.error(
      `[Error Debug] resultData.return_value:`,
      resultData.return_value
    );

    let errorMessage = `Unknown error during ${taskName} task execution`;

    // Try to extract error message from various possible locations
    if (resultData.error) {
      if (typeof resultData.error === 'string') {
        errorMessage = resultData.error;
      } else if (typeof resultData.error === 'object') {
        // Handle case where error is an object
        errorMessage =
          resultData.error.message ||
          resultData.error.detail ||
          JSON.stringify(resultData.error);
      }
    } else if (resultData.result && resultData.result.error) {
      if (typeof resultData.result.error === 'string') {
        errorMessage = resultData.result.error;
      } else if (typeof resultData.result.error === 'object') {
        // Handle case where error is an object
        errorMessage =
          resultData.result.error.message ||
          resultData.result.error.detail ||
          JSON.stringify(resultData.result.error);
      }
    } else if (resultData.return_value) {
      // Sometimes the error might be in return_value as a JSON string
      try {
        const parsed = JSON.parse(resultData.return_value);
        if (parsed.error) {
          errorMessage =
            typeof parsed.error === 'string'
              ? parsed.error
              : parsed.error.message ||
                parsed.error.detail ||
                JSON.stringify(parsed.error);
        }
      } catch (parseErr) {
        // If return_value is not JSON, use it as is if it looks like an error
        if (
          resultData.return_value &&
          (resultData.return_value.includes('Error') ||
            resultData.return_value.includes('Cannot'))
        ) {
          errorMessage = resultData.return_value;
        }
      }
    }

    // Additional check: if we still have a generic message, try to extract from any nested structures
    if (errorMessage === `Unknown error during ${taskName} task execution`) {
      // Check if there's any string in the resultData that looks like an error message
      const searchForError = (obj, path = '') => {
        if (
          typeof obj === 'string' &&
          (obj.includes('Cannot') ||
            obj.includes('Error') ||
            obj.includes('Failed'))
        ) {
          return obj;
        }
        if (typeof obj === 'object' && obj !== null) {
          for (const [key, value] of Object.entries(obj)) {
            const found = searchForError(value, path ? `${path}.${key}` : key);
            if (found) return found;
          }
        }
        return null;
      };

      const foundError = searchForError(resultData);
      if (foundError) {
        errorMessage = foundError;
      }
    }

    throw new Error(errorMessage);
  }

  let data = {};
  if (resultData.status === 'SUCCEEDED' && resultData.return_value) {
    try {
      data = JSON.parse(resultData.return_value);
      console.log(`Successfully parsed ${taskName} data:`, data);
    } catch (parseError) {
      console.error(
        `Failed to parse ${taskName} data from return_value:`,
        parseError,
        'Raw return_value:',
        resultData.return_value
      );
      throw new Error(
        `Failed to parse ${taskName} data for request ID ${requestId}: ${parseError.message}`
      );
    }
  } else if (resultData.result) {
    console.warn(
      `Using resultData.result as fallback for ${taskName} status ${resultData.status}`
    );
    data = resultData.result;
  }

  return data;
}

// Update workspace configuration
export async function updateWorkspace(workspaceName, config) {
  try {
    console.log(`Updating workspace ${workspaceName} with config:`, config);

    const scheduleResponse = await apiClient.post(`/workspaces/update`, {
      workspace_name: workspaceName,
      config: config,
    });

    if (!scheduleResponse.ok) {
      throw new Error(
        `Error scheduling updateWorkspace: ${scheduleResponse.statusText} (status ${scheduleResponse.status})`
      );
    }

    const requestId = scheduleResponse.headers.get('X-Skypilot-Request-ID');
    if (!requestId) {
      throw new Error('Failed to obtain request ID for updateWorkspace');
    }

    return await pollForTaskCompletion(requestId, 'updateWorkspace');
  } catch (error) {
    console.error('Failed to update workspace:', error);
    throw error;
  }
}

// Create new workspace
export const createWorkspace = async (workspaceName, config) => {
  try {
    const scheduleResponse = await apiClient.post(`/workspaces/create`, {
      workspace_name: workspaceName,
      config: config,
    });

    if (!scheduleResponse.ok) {
      const errorText = await scheduleResponse.text();
      throw new Error(
        `Error scheduling createWorkspace: ${scheduleResponse.statusText} (status ${scheduleResponse.status})`
      );
    }

    const requestId = scheduleResponse.headers.get('X-Skypilot-Request-ID');
    if (!requestId) {
      throw new Error('Failed to obtain request ID for createWorkspace');
    }

    return await pollForTaskCompletion(requestId, 'createWorkspace');
  } catch (error) {
    console.error('Failed to create workspace:', error);
    throw error;
  }
};

// Delete workspace
export async function deleteWorkspace(workspaceName) {
  try {
    console.log(`Deleting workspace ${workspaceName}`);

    const scheduleResponse = await apiClient.post(`/workspaces/delete`, {
      workspace_name: workspaceName,
    });

    if (!scheduleResponse.ok) {
      throw new Error(
        `Error scheduling deleteWorkspace: ${scheduleResponse.statusText} (status ${scheduleResponse.status})`
      );
    }

    const requestId = scheduleResponse.headers.get('X-Skypilot-Request-ID');
    if (!requestId) {
      throw new Error('Failed to obtain request ID for deleteWorkspace');
    }

    console.log(
      `[Delete Debug] Got request ID for deleteWorkspace: ${requestId}`
    );

    try {
      const result = await pollForTaskCompletion(requestId, 'deleteWorkspace');
      console.log(
        `[Delete Debug] deleteWorkspace completed successfully:`,
        result
      );
      return result;
    } catch (pollError) {
      console.error(
        `[Delete Debug] deleteWorkspace failed with error:`,
        pollError
      );
      console.error(`[Delete Debug] Error message:`, pollError.message);
      throw pollError;
    }
  } catch (error) {
    console.error('Failed to delete workspace:', error);
    throw error;
  }
}

// Get entire SkyPilot configuration
export async function getConfig() {
  try {
    console.log('Getting entire SkyPilot configuration');

    const scheduleResponse = await apiClient.get(`/workspaces/config`);
    if (!scheduleResponse.ok) {
      throw new Error(
        `Error scheduling getConfig: ${scheduleResponse.statusText} (status ${scheduleResponse.status})`
      );
    }

    const requestId = scheduleResponse.headers.get('X-Skypilot-Request-ID');
    if (!requestId) {
      throw new Error('Failed to obtain request ID for getConfig');
    }

    return await pollForTaskCompletion(requestId, 'getConfig');
  } catch (error) {
    console.error('Failed to get config:', error);
    throw error;
  }
}

// Update entire SkyPilot configuration
export async function updateConfig(config) {
  try {
    console.log('Updating entire SkyPilot configuration with config:', config);

    const scheduleResponse = await apiClient.post(`/workspaces/config`, {
      config: config,
    });

    if (!scheduleResponse.ok) {
      throw new Error(
        `Error scheduling updateConfig: ${scheduleResponse.statusText} (status ${scheduleResponse.status})`
      );
    }

    const requestId = scheduleResponse.headers.get('X-Skypilot-Request-ID');
    if (!requestId) {
      throw new Error('Failed to obtain request ID for updateConfig');
    }

    return await pollForTaskCompletion(requestId, 'updateConfig');
  } catch (error) {
    console.error('Failed to update config:', error);
    throw error;
  }
}
