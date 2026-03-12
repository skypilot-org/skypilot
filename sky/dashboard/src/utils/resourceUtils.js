/**
 * Resource utility functions for formatting CPU and memory values
 */

/**
 * Format CPU count for display
 * @param {number|null|undefined} cpu - CPU count value
 * @returns {string} - Formatted CPU string (integer if whole, 1 decimal otherwise, or '-')
 */
export function formatCpu(cpu) {
  if (cpu === null || cpu === undefined) return '-';
  return Math.round(cpu).toString();
}

/**
 * Format memory in GB for display
 * @param {number|null|undefined} memory - Memory value in GB
 * @returns {string} - Formatted memory string (e.g., "16.0 GB" or "-")
 */
export function formatMemory(memory) {
  if (memory === null || memory === undefined) return '-';
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
