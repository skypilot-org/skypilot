/**
 * Resource utility functions for formatting CPU and memory values
 */

/**
 * Format CPU value for display, matching CLI format_float with precision=0
 * Rounds to 0 decimal places (removes .0 for whole numbers, rounds non-whole numbers to integers)
 * @param {number|null|undefined} cpu - CPU count value
 * @returns {string} - Formatted CPU string (e.g., "16" or "256" or "-")
 */
export function formatCpuValue(cpu) {
  if (cpu === null || cpu === undefined) return '-';
  // Round to 0 decimal places, matching CLI format_float with precision=0
  // This removes .0 for whole numbers and rounds non-whole numbers to integers
  return Math.round(cpu).toString();
}

/**
 * Format CPU count for display
 * @param {number|null|undefined} cpu - CPU count value
 * @returns {string} - Formatted CPU string (e.g., "16" or "256" or "-")
 */
export function formatCpu(cpu) {
  if (cpu === null || cpu === undefined) return '-';
  return formatCpuValue(cpu);
}

/**
 * Format memory value (without unit) for display, matching CLI format_float with precision=0
 * Rounds to 0 decimal places (removes .0 for whole numbers, rounds non-whole numbers to integers)
 * @param {number|null|undefined} memory - Memory value in GB
 * @returns {string} - Formatted memory string (e.g., "16" or "2000" or "-")
 */
export function formatMemoryValue(memory) {
  if (memory === null || memory === undefined) return '-';
  // Round to 0 decimal places, matching CLI format_float with precision=0
  // This removes .0 for whole numbers and rounds non-whole numbers to integers
  return Math.round(memory).toString();
}

/**
 * Format memory in GB for display
 * @param {number|null|undefined} memory - Memory value in GB
 * @returns {string} - Formatted memory string (e.g., "16 GB" or "2000 GB" or "-")
 */
export function formatMemory(memory) {
  if (memory === null || memory === undefined) return '-';
  const formatted = formatMemoryValue(memory);
  return formatted === '-' ? '-' : `${formatted} GB`;
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
