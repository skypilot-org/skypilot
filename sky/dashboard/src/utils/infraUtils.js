/**
 * Infrastructure utility functions for context key generation
 */

/**
 * Builds a context stats key for use in contextStats mapping.
 * This key is used consistently across the application to identify
 * infrastructure contexts (Kubernetes, SSH Node Pools, Slurm clusters).
 *
 * @param {string} contextName - The context name (e.g., 'my-context', 'ssh-pool1', 'slurm-cluster')
 * @param {Object} options - Options for determining the context type
 * @param {boolean} [options.isSSH] - Whether this is an SSH Node Pool context
 * @param {boolean} [options.isSlurm] - Whether this is a Slurm cluster context
 * @param {string} [options.cloud] - Cloud type ('Kubernetes', 'SSH', 'slurm', or 'Slurm')
 * @returns {string} - The context stats key (e.g., 'kubernetes/my-context', 'ssh/pool1', 'slurm/cluster')
 */
export function buildContextStatsKey(contextName, options = {}) {
  if (!contextName) {
    return null;
  }

  const { isSSH, isSlurm, cloud } = options;

  // Determine context type from options or infer from context name
  let contextType = null;
  if (isSSH || cloud === 'SSH') {
    contextType = 'ssh';
  } else if (isSlurm || cloud?.toLowerCase() === 'slurm') {
    contextType = 'slurm';
  } else if (cloud === 'Kubernetes') {
    contextType = 'kubernetes';
  } else if (contextName.startsWith('ssh-')) {
    // Infer from context name if no explicit type provided
    contextType = 'ssh';
  } else {
    // Default to Kubernetes for backward compatibility
    contextType = 'kubernetes';
  }

  // Process context name based on type
  let processedName = contextName;
  if (contextType === 'ssh') {
    // Remove 'ssh-' prefix if present
    processedName = contextName.replace(/^ssh-/, '');
  }

  return `${contextType}/${processedName}`;
}

/**
 * Builds a context stats key from a cloud/region pair (used in jobs and clusters).
 * This is a convenience wrapper around buildContextStatsKey for the common
 * pattern where we have a cloud type and region/context name.
 *
 * @param {string} cloud - Cloud type ('Kubernetes', 'SSH', 'slurm', or 'Slurm')
 * @param {string} region - Region/context name (may include 'ssh-' prefix for SSH)
 * @returns {string|null} - The context stats key or null if invalid
 */
export function buildContextStatsKeyFromCloud(cloud, region) {
  if (!cloud || !region) {
    return null;
  }
  return buildContextStatsKey(region, { cloud });
}
