/**
 * User utility functions for the SkyPilot dashboard
 */

/**
 * Check if a user is a service account based on their user hash
 * @param {string} userHash - The user hash to check
 * @returns {boolean} - True if the user is a service account, false otherwise
 */
export function isServiceAccount(userHash) {
  if (!userHash || typeof userHash !== 'string') {
    return false;
  }
  // Service accounts have user_hash starting with 'sa-'
  return userHash.toLowerCase().startsWith('sa-');
}

/**
 * Generate a user link based on whether it's a service account or regular user
 * @param {string} userHash - The user hash to generate a link for
 * @returns {string} - The appropriate URL path for the user
 */
export function getUserLink(userHash) {
  // Regular users link to the main users page
  return '/users';
}
