import { ENDPOINT } from './constants';
import { getClusters } from './clusters';
import { getManagedJobs } from './jobs';

// Helper functions for username parsing
const parseUsername = (username) => {
  if (username && username.includes('@')) {
    return username.split('@')[0];
  }
  return username || 'N/A';
};

const getFullEmail = (username) => {
  if (username && username.includes('@')) {
    return username;
  }
  return '-';
};

// Mock data for now - replace with actual API call
const mockUsers = [
  { username: 'user1', last_login: '2023-01-15T10:00:00Z' },
  { username: 'user2', last_login: '2023-01-16T12:30:00Z' },
  { username: 'user3', last_login: '2023-01-17T15:45:00Z' },
];

export async function getUsers() {
  try {
    const response = await fetch(`${ENDPOINT}/users`);
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
      usernameDisplay: parseUsername(user.username),
      fullEmail: getFullEmail(user.username),
      clusterCount: userClusters.length,
      jobCount: userJobs.length,
    };
  });

  return processedUsers;
}
