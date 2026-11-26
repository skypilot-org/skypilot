/**
 * Where a dynamic task's own job page redirects: task <index> of its job
 * group, e.g. /jobs/66/2 for job 67. Keeps the page's other query parameters
 * (?tab=logs, the ?node_rdma preview flag, ...), without the route's own `job`
 * parameter.
 *
 * @param {number|string} rootJobId - The job group's job ID
 * @param {number|string} taskIndex - The task's index in its job group
 * @param {Object} query - The current page's router.query
 * @returns {string} The task page's URL
 */
export function dynamicTaskUrl(rootJobId, taskIndex, query) {
  const params = new URLSearchParams();
  Object.entries(query || {}).forEach(([key, value]) => {
    if (key === 'job') return;
    (Array.isArray(value) ? value : [value]).forEach((v) =>
      params.append(key, v)
    );
  });
  const search = params.toString();
  return `/jobs/${rootJobId}/${taskIndex}${search ? `?${search}` : ''}`;
}
