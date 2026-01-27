/**
 * YAML Hub API connector
 *
 * Provides functions for managing YAML templates including CRUD operations,
 * pinning, and deployment.
 */

import { apiClient } from './client';

/**
 * List YAML templates with optional filters.
 * By default returns all pinned templates plus templates owned by the current user.
 *
 * @param {Object} options - Filter options
 * @param {string} [options.category] - Filter by category
 * @param {boolean} [options.pinnedOnly] - Only return pinned templates
 * @param {boolean} [options.myRecipesOnly] - Only return user's own templates
 * @param {string} [options.recipeType] - Filter by type ('cluster' or 'job')
 * @returns {Promise<Array>} List of recipes
 */
export async function getRecipes(options = {}) {
  try {
    const body = {
      category: options.category || null,
      pinned_only: options.pinnedOnly || false,
      my_recipes_only: options.myRecipesOnly || false,
      recipe_type: options.recipeType || null,
    };

    const result = await apiClient.fetch('/recipes/list', body, 'POST');
    return result || [];
  } catch (error) {
    console.error('Error fetching YAML templates:', error);
    throw error;
  }
}

/**
 * Get a single YAML template by ID.
 *
 * @param {string} recipeId - The recipe's unique ID
 * @returns {Promise<Object|null>} Recipe object or null if not found
 */
export async function getRecipe(recipeId) {
  try {
    const result = await apiClient.fetch('/recipes/get', {
      recipe_id: recipeId,
    });
    return result;
  } catch (error) {
    console.error('Error fetching Recipe:', error);
    throw error;
  }
}

/**
 * Create a new Recipe.
 *
 * @param {Object} data - Recipe data
 * @param {string} data.name - Display name
 * @param {string} data.content - YAML content
 * @param {string} data.recipeType - Type ('cluster' or 'job')
 * @param {string} [data.description] - Optional description
 * @param {string} [data.category] - Optional category
 * @param {string} [data.ownerName] - Optional owner name (for unauthenticated users)
 * @returns {Promise<Object>} Created recipe object
 */
export async function createRecipe(data) {
  try {
    const result = await apiClient.fetch('/recipes/create', {
      name: data.name,
      content: data.content,
      recipe_type: data.recipeType,
      description: data.description || null,
      category: data.category || null,
      owner_name: data.ownerName || null,
    });
    return result;
  } catch (error) {
    console.error('Error creating Recipe:', error);
    throw error;
  }
}

/**
 * Update an existing Recipe.
 * Only the owner can update their template.
 *
 * @param {string} recipeId - The recipe's unique ID
 * @param {Object} data - Fields to update
 * @param {string} [data.name] - New name
 * @param {string} [data.description] - New description
 * @param {string} [data.content] - New YAML content
 * @param {string} [data.category] - New category
 * @returns {Promise<Object|null>} Updated recipe or null if not authorized
 */
export async function updateRecipe(recipeId, data) {
  try {
    const result = await apiClient.fetch('/recipes/update', {
      recipe_id: recipeId,
      name: data.name,
      description: data.description,
      content: data.content,
      category: data.category,
    });
    return result;
  } catch (error) {
    console.error('Error updating Recipe:', error);
    throw error;
  }
}

/**
 * Delete a Recipe.
 * Only the owner can delete their recipe.
 *
 * @param {string} recipeId - The recipe's unique ID
 * @returns {Promise<boolean>} True if deleted successfully
 */
export async function deleteRecipe(recipeId) {
  try {
    const result = await apiClient.fetch('/recipes/delete', {
      recipe_id: recipeId,
    });
    return result;
  } catch (error) {
    console.error('Error deleting Recipe:', error);
    throw error;
  }
}

/**
 * Toggle pin status of a Recipe.
 * Admin only operation.
 *
 * @param {string} recipeId - The recipe's unique ID
 * @param {boolean} pinned - New pinned status
 * @returns {Promise<Object|null>} Updated recipe or null if not found
 */
export async function togglePinRecipe(recipeId, pinned) {
  try {
    const result = await apiClient.fetch('/recipes/pin', {
      recipe_id: recipeId,
      pinned: pinned,
    });
    return result;
  } catch (error) {
    console.error('Error toggling Recipe pin status:', error);
    throw error;
  }
}

/**
 * Get all available categories (grouped).
 *
 * @returns {Promise<Object>} Object with 'predefined', 'custom', and 'all' arrays
 */
export async function getCategories() {
  try {
    const response = await apiClient.fetchImmediate(
      '/recipes/categories',
      {},
      'GET'
    );
    if (!response.ok) {
      throw new Error(`Failed to fetch categories: ${response.status}`);
    }
    const id = response.headers.get('X-Skypilot-Request-ID');
    if (!id) {
      throw new Error('No request ID received');
    }
    const fetchedData = await apiClient.get(`/api/get?request_id=${id}`);
    if (!fetchedData.ok) {
      throw new Error(`Failed to get categories result: ${fetchedData.status}`);
    }
    const data = await fetchedData.json();
    return data.return_value
      ? JSON.parse(data.return_value)
      : { predefined: [], custom: [], all: [] };
  } catch (error) {
    console.error('Error fetching categories:', error);
    throw error;
  }
}

/**
 * Get all categories as a flat list.
 *
 * @returns {Promise<Array>} Array of category objects
 */
export async function getAllCategories() {
  try {
    const response = await apiClient.fetchImmediate(
      '/recipes/categories/list',
      {},
      'GET'
    );
    if (!response.ok) {
      throw new Error(`Failed to fetch categories: ${response.status}`);
    }
    const id = response.headers.get('X-Skypilot-Request-ID');
    if (!id) {
      throw new Error('No request ID received');
    }
    const fetchedData = await apiClient.get(`/api/get?request_id=${id}`);
    if (!fetchedData.ok) {
      throw new Error(`Failed to get categories result: ${fetchedData.status}`);
    }
    const data = await fetchedData.json();
    return data.return_value ? JSON.parse(data.return_value) : [];
  } catch (error) {
    console.error('Error fetching all categories:', error);
    throw error;
  }
}