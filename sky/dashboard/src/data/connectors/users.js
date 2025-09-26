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
      })) || []
    );
  } catch (error) {
    console.error('Failed to fetch users:', error);
    return []; // Return empty array on error
  }
}
