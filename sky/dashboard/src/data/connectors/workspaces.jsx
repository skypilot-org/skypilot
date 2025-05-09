import { API_URL } from './constants';

export async function getWorkspaceConfig() {
  try {
    const response = await fetch(`${API_URL}/workspace_config`);
    if (!response.ok) {
      throw new Error(`Error fetching workspace config: ${response.statusText}`);
    }
    const data = await response.json();
    // The backend returns an object where keys are request IDs.
    // We need to find the most recent successful request for 'workspace_config'.
    // Assuming the response is an object of request payloads,
    // and each payload has a 'status' and 'result' (if successful).
    // The actual result is nested under request_task.result.
    // This logic might need adjustment based on the exact structure returned by api_get for this endpoint.
    
    // Find the latest successful request_task containing the workspace config data.
    // The server.py uses executor.schedule_request, which means the response from /api/v1/workspace_config
    // will immediately return, and we need to poll /api/get/{request_id} for the actual result.
    // For simplicity here, we'll assume the initial response from /workspace_config 
    // directly contains the data or a way to get it.
    // If it's an async operation, the frontend would typically poll /api/get/{request_id}
    // as shown in other parts of SkyPilot's server interactions.
    // Given the current server.py, func=skypilot_config.get_workspace_config is scheduled,
    // and the client is expected to use /api/get.
    // However, the dashboard usually fetches data more directly.
    // Let's assume for now the dashboard connector simplifies this,
    // or there's a direct way to get the config.
    // The server returns the result of func directly if it's a short schedule.
    // server.py: func=skypilot_config.get_workspace_config, schedule_type=requests_lib.ScheduleType.SHORT
    // This means the result should be available via /api/get/{request_id} fairly quickly.
    // The FastAPI endpoint for /workspace_config itself doesn't return the data directly, it schedules.
    // The dashboard's getClusters/getManagedJobs also seem to hit endpoints that schedule,
    // but their connectors directly parse the result from the initial fetch.
    // This implies the actual data might be embedded or that these connectors handle polling.
    
    // For now, let's assume `data` from fetch is the actual result from `skypilot_config.get_workspace_config()`.
    // This function returns get_nested(('workspaces',), default_value={})
    // So, data should be an object like: { "ws1": {details...}, "ws2": {details...} } or just {}
    return data.result || {}; // Accessing data.result based on typical SkyPilot API responses for scheduled tasks
  } catch (error) {
    console.error('Failed to fetch workspace config:', error);
    throw error;
  }
} 
