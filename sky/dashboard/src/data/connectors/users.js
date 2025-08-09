import { getClusters } from '@/data/connectors/clusters';
import { getManagedJobs } from '@/data/connectors/jobs';
import { apiClient } from '@/data/connectors/client';

// Helper functions for username parsing
const parseUsername = (username, userId) => {
  if (username && username.includes('@')) {
    return username.split('@')[0];
  }
  // If no email, show username with userId in parentheses only if they're different
  const usernameBase = username || 'N/A';

  // Skip showing userId if it's the same as username
  if (userId && userId !== usernameBase) {
    return `${usernameBase} (${userId})`;
  }

  return usernameBase;
};

const getFullEmail = (username) => {
  if (username && username.includes('@')) {
    return username;
  }
  return '-';
};

export async function getUsers() {
  try {
    const response = await apiClient.get(`/users`);
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }
    const data = await response.json();
    // Data from API is: [{ id: 'user_hash', name: 'username' }, ...]
    // Transform to: [{ userId: 'user_hash', username: 'username' }, ...]
    return (
      data.map((user) => ({
        userId: user.id,
        username: user.name,
        role: user.role,
        created_at: user.created_at,
        token_id: user.token_id,
        last_used_at: user.last_used_at,
        expires_at: user.expires_at,
      })) || []
    );
  } catch (error) {
    console.error('Failed to fetch users:', error);
    return []; // Return empty array on error
  }
}

/**
 * Composite function to fetch all users data with cluster and job counts atomically
 * This prevents cache invalidation issues when switching between pages
 */
export async function getUsersWithCounts() {
  const [usersData, clustersData, jobsResponse] = await Promise.all([
    getUsers(),
    getClusters(),
    getManagedJobs(),
  ]);

  const jobsData = jobsResponse.jobs || [];

  const processedUsers = (usersData || []).map((user) => {
    const userClusters = (clustersData || []).filter(
      (c) => c.user_hash === user.userId // Match by hash only
    );
    const userJobs = (jobsData || []).filter(
      (j) => j.user_hash === user.userId // Match by hash only
    );
    return {
      ...user,
      usernameDisplay: parseUsername(user.username, user.userId),
      fullEmail: getFullEmail(user.username),
      clusterCount: userClusters.length,
      jobCount: userJobs.length,
    };
  });

  return processedUsers;
}
