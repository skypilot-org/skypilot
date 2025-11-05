import { apiClient } from '@/data/connectors/client';

export async function getUsers() {
  try {
    const response = await apiClient.get(`/users`);
    if (!response.ok) {
      throw new Error(`Failed to fetch users with status ${response.status}`);
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
    throw error;
  }
}
