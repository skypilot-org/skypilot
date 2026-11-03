/**
 * Resource utility functions for formatting CPU and memory values
 */

/**
 * Thresholds for switching to large-unit display. Counts at or above these
 * render as "<n>.<d> k/TB" so five- and six-digit numbers stay one glance
 * wide: >=10000 CPU ("44.2k"), >=10000 GB memory ("665.8 TB"; 1 TB = 1024 GB).
 */
const CPU_K_THRESHOLD = 10000;
const MEMORY_TB_THRESHOLD_GB = 10000;

/**
 * Format CPU count for display
 * @param {number|null|undefined} cpu - CPU count value
 * @returns {string} - Formatted CPU string: "<n>.<d>k" at or above 10000,
 *   integer otherwise, or '-' for null/undefined
 */
export function formatCpu(cpu) {
  if (cpu === null || cpu === undefined) return '-';
  if (cpu >= CPU_K_THRESHOLD) {
    return `${(cpu / 1000).toFixed(1)}k`;
  }
  return Math.round(cpu).toString();
}

/**
 * Format memory in GB for display
 * @param {number|null|undefined} memory - Memory value in GB
 * @returns {string} - Formatted memory string: "<n>.<d> TB" at or above
 *   10000 GB, "<n> GB" otherwise, or '-' for null/undefined
 */
export function formatMemory(memory) {
  if (memory === null || memory === undefined) return '-';
  if (memory >= MEMORY_TB_THRESHOLD_GB) {
    return `${(memory / 1024).toFixed(1)} TB`;
  }
  return `${Math.round(memory)} GB`;
}

/**
 * Calculate aggregated resource value from nodes
 * @param {Array} nodes - Array of node objects
 * @param {string} resourceKey - Key to extract from each node (e.g., 'cpu_count', 'memory_gb')
 * @param {boolean} hasNodeData - Whether node data is available
 * @returns {number|null} - Aggregated value or null if no data
 */
export function calculateAggregatedResource(nodes, resourceKey, hasNodeData) {
  if (!hasNodeData || nodes.length === 0) return null;
  const total = nodes.reduce((sum, node) => {
    const value = node[resourceKey];
    return sum + (value !== null && value !== undefined ? value : 0);
  }, 0);
  return total > 0 ? total : null;
}
