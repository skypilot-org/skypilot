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
 * @param {boolean} [options.pinnedOnly] - Only return pinned templates
 * @param {boolean} [options.myRecipesOnly] - Only return user's own templates
 * @param {string} [options.recipeType] - Filter by type (see RecipeType in constants/recipeTypes.js)
 * @returns {Promise<Array>} List of recipes
 */
export async function getRecipes(options = {}) {
  try {
    const body = {
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
 * Get a single recipe by name.
 *
 * @param {string} recipeName - The recipe's unique name
 * @returns {Promise<Object|null>} Recipe object or null if not found
 */
export async function getRecipe(recipeName) {
  try {
    const result = await apiClient.fetch('/recipes/get', {
      recipe_name: recipeName,
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
 * @param {string} data.recipeType - Type (see RecipeType in constants/recipeTypes.js)
 * @param {string} [data.description] - Optional description
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
 * Note: Recipe names cannot be changed as they are the unique identifier.
 *
 * @param {string} recipeName - The recipe's unique name
 * @param {Object} data - Fields to update
 * @param {string} [data.description] - New description
 * @param {string} [data.content] - New YAML content
 * @returns {Promise<Object|null>} Updated recipe or null if not authorized
 */
export async function updateRecipe(recipeName, data) {
  try {
    const result = await apiClient.fetch('/recipes/update', {
      recipe_name: recipeName,
      description: data.description,
      content: data.content,
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
 * @param {string} recipeName - The recipe's unique name
 * @returns {Promise<boolean>} True if deleted successfully
 */
export async function deleteRecipe(recipeName) {
  try {
    const result = await apiClient.fetch('/recipes/delete', {
      recipe_name: recipeName,
    });
    return result;
  } catch (error) {
    console.error('Error deleting Recipe:', error);
    throw error;
  }
}

/**
 * Toggle pin status of a Recipe.
 *
 * @param {string} recipeName - The recipe's unique name
 * @param {boolean} pinned - New pinned status
 * @returns {Promise<Object|null>} Updated recipe or null if not found
 */
export async function togglePinRecipe(recipeName, pinned) {
  try {
    const result = await apiClient.fetch('/recipes/pin', {
      recipe_name: recipeName,
      pinned: pinned,
    });
    return result;
  } catch (error) {
    console.error('Error toggling Recipe pin status:', error);
    throw error;
  }
}
