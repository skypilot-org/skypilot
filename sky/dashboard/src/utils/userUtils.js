/**
 * User-related utility functions
 * Centralized functions for user display, formatting, and navigation
 */

/**
 * Helper function to generate user link based on whether it's a service account
 * @param {string} userHash - The user hash to determine the link destination
 * @returns {string} - The appropriate URL path
 */
export const getUserLink = (userHash) => {
  // Service accounts have user_hash starting with 'sa-'
  if (userHash && userHash.startsWith('sa-')) {
    return '/users?tab=service-accounts';
  }
  return '/users';
};

/**
 * Helper function to format username for display
 * @param {string} username - The username (often an email)
 * @param {string} userId - The user ID/hash
 * @returns {string} - Formatted display name
 */
export const formatUserDisplay = (username, userId) => {
  if (username && username.includes('@')) {
    const emailPrefix = username.split('@')[0];
    // Show email prefix with userId if they're different
    if (userId && userId !== emailPrefix) {
      return `${emailPrefix} (${userId})`;
    }
    return emailPrefix;
  }
  // If no email, show username with userId in parentheses only if they're different
  const usernameBase = username || userId || 'N/A';

  // Skip showing userId if it's the same as username
  if (userId && userId !== usernameBase) {
    return `${usernameBase} (${userId})`;
  }

  return usernameBase;
};

/**
 * Helper function to check if a user is a service account
 * @param {string} userHash - The user hash to check
 * @returns {boolean} - True if the user is a service account
 */
export const isServiceAccount = (userHash) => {
  return userHash && userHash.startsWith('sa-');
};