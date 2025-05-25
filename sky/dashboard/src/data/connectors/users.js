import { ENDPOINT } from './constants';

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
