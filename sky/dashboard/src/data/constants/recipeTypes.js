/**
 * Recipe type constants for the Recipe Hub.
 *
 * These values must match the RecipeType enum in sky/recipes/utils.py
 */

import {
  ServerIcon,
  BriefcaseIcon,
  DatabaseIcon,
  LayersIcon,
  FileCodeIcon,
} from 'lucide-react';

export const RecipeType = Object.freeze({
  CLUSTER: 'cluster',
  JOB: 'job',
  POOL: 'pool',
  VOLUME: 'volume',
});

/**
 * List of all valid recipe types.
 */
export const ALL_RECIPE_TYPES = Object.freeze(Object.values(RecipeType));

/**
 * Check if a string is a valid recipe type.
 * @param {string} value - The value to check
 * @returns {boolean} True if valid recipe type
 */
export function isValidRecipeType(value) {
  return ALL_RECIPE_TYPES.includes(value);
}

/**
 * Helper to capitalize first letter of each word.
 * @param {string} str - The string to capitalize
 * @returns {string} Capitalized string
 */
export function capitalizeWords(str) {
  if (!str) return '';
  return str
    .split(' ')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(' ');
}

/**
 * Get icon, color, and label information for a recipe type.
 * @param {string} recipeType - The recipe type value
 * @returns {Object} Object with icon, color, label (short), and fullLabel properties
 */
export function getRecipeTypeInfo(recipeType) {
  switch (recipeType) {
    case RecipeType.CLUSTER:
      return {
        icon: ServerIcon,
        color: 'sky',
        label: 'Cluster',
        fullLabel: 'Cluster',
      };
    case RecipeType.JOB:
      return {
        icon: BriefcaseIcon,
        color: 'purple',
        label: 'Job',
        fullLabel: 'Managed Job',
      };
    case RecipeType.VOLUME:
      return {
        icon: DatabaseIcon,
        color: 'green',
        label: 'Volume',
        fullLabel: 'Volume',
      };
    case RecipeType.POOL:
      return {
        icon: LayersIcon,
        color: 'orange',
        label: 'Pool',
        fullLabel: 'Job Pool',
      };
    default:
      throw new Error(`Invalid recipe type: ${recipeType}`);
  }
}

/**
 * Generate the CLI launch command for a recipe.
 * @param {string} recipeType - The recipe type value
 * @param {string} recipeName - The recipe's unique name
 * @returns {string} The CLI command to launch this recipe
 */
export function getLaunchCommand(recipeType, recipeName) {
  switch (recipeType) {
    case RecipeType.CLUSTER:
      return `sky launch recipes:${recipeName}`;
    case RecipeType.JOB:
      return `sky jobs launch recipes:${recipeName}`;
    case RecipeType.VOLUME:
      return `sky volumes apply recipes:${recipeName}`;
    case RecipeType.POOL:
      return `sky jobs pool apply recipes:${recipeName}`;
    default:
      throw new Error(`Invalid recipe type: ${recipeType}`);
  }
}
