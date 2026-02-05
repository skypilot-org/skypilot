import { apiClient } from '@/data/connectors/client';

export async function getServiceAccountTokens() {
  try {
    const response = await apiClient.get('/users/service-account-tokens');
    if (!response.ok) {
      throw new Error(
        `Failed to fetch service account tokens with status ${response.status}`
      );
    }
    const data = await response.json();
    return data || [];
  } catch (error) {
    console.error('Failed to fetch service account tokens:', error);
    throw error;
  }
}

export async function getUsers() {
  try {
    const response = await apiClient.get(`/users`);
    if (!response.ok) {
      throw new Error(`Failed to fetch users with status ${response.status}`);
    }
    const data = await response.json();
    // Data from API is: [{ id: 'user_hash', name: 'username' }, ...]
    // Transform to: [{ userId: 'user_hash', username: 'username' }, ...]
    // Filter out the dashboard users
    return (data || [])
      .filter(
        (user) =>
          !(
            ['dashboard', 'local'].includes(user.name) &&
            user.user_type === 'legacy'
          )
      )
      .map((user) => ({
        userId: user.id,
        username: user.name,
        role: user.role,
        created_at: user.created_at,
        userType: user.user_type,
      }));
  } catch (error) {
    console.error('Failed to fetch users:', error);
    throw error;
  }
}
