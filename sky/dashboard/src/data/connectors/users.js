import { apiClient } from '@/data/connectors/client';

/**
 * Whether a server-side pagination plugin is available for SA tokens.
 * The pagination plugin sets this on window when its bundle is loaded.
 */
export function isServiceAccountTokensPaginationAvailable() {
  return (
    typeof window !== 'undefined' &&
    typeof window.__skyServiceAccountTokensPaginationFetch === 'function'
  );
}

/**
 * Server-paginated SA tokens fetch. Returns { items, total, page, limit,
 * total_pages, has_next, has_prev }. Throws if the pagination plugin is
 * not installed — callers should gate on
 * isServiceAccountTokensPaginationAvailable().
 */
export async function getServiceAccountTokensPaginated({
  page = 1,
  limit = 50,
  search = '',
  sortBy = 'created_at',
  sortOrder = 'desc',
} = {}) {
  const fetcher =
    typeof window !== 'undefined' &&
    window.__skyServiceAccountTokensPaginationFetch;
  if (typeof fetcher !== 'function') {
    throw new Error('Service account tokens pagination plugin not available');
  }
  return fetcher({ page, limit, search, sortBy, sortOrder });
}

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
