import { ENDPOINT } from './constants';

export async function getWorkspaces() {
  try {
    // Step 1: Call the /workspaces endpoint to schedule the task
    const scheduleResponse = await fetch(`${ENDPOINT}/workspaces`);
    if (!scheduleResponse.ok) {
      throw new Error(`Error scheduling getWorkspaces: ${scheduleResponse.statusText} (status ${scheduleResponse.status})`);
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
          console.log('Found request_id in /workspaces response body (fallback):', requestId);
        } else {
          // If not in header AND not in body after attempting fallback.
          throw new Error(
            'X-Skypilot-Request-ID header not found AND request_id not found in parsed response body from /workspaces.'
          );
        }
      } catch (e) {
        // This catch handles errors from scheduleResponse.json() or the explicit throw above.
        const errorMessage = e.message || 'Error processing fallback for request_id from /workspaces response body.';
        console.error('Error in /workspaces request_id fallback logic:', errorMessage);
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
    const resultResponse = await fetch(`${ENDPOINT}/api/get?request_id=${requestId}`);
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
            } else if (parsedDetail && parsedDetail.result && parsedDetail.result.error) {
              innerDetail = parsedDetail.result.error;
            }
          } catch (parseErr) { /* Inner detail is not JSON, use as is */ }
          errorDetail = `Error fetching workspace data for request ID ${requestId}: ${innerDetail}`;
        }
      } catch (e) { /* ignore error parsing errorData */ }
      throw new Error(errorDetail);
    }

    const resultData = await resultResponse.json();

    // Step 4: Check task status and return the actual workspace data
    if (resultData.status === 'FAILED') {
      const errorMessage = resultData.error || (resultData.result && resultData.result.error) || 'Unknown error during task execution';
      throw new Error(`Fetching workspace data failed for request ID ${requestId}: ${errorMessage}`);
    }
    
    if (resultData.status !== 'SUCCESSFUL' && resultData.status !== 'COMPLETED') {
        // Add a log for unexpected statuses that aren't explicitly 'FAILED'
        // as /api/get is expected to wait until terminal state.
        console.warn(`Received unexpected status '${resultData.status}' for request ID ${requestId}. Proceeding with result if available.`);
    }

    // The resultData.result should contain the workspace configuration object.
    // Example: { "ws1": {...}, "ws2": {...} } or just {}
    console.log('Successfully fetched workspace data:', resultData.result);
    return resultData.result || {};
  } catch (error) {
    console.error('Failed to fetch workspaces (in getWorkspaces function):', error.message, error.stack);
    throw error; // Re-throw to allow UI to handle it
  }
} 
