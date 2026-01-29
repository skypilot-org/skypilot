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
  FileCode,
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
 * Map color names to Tailwind text color classes.
 */
const COLOR_CLASS_MAP = {
  sky: 'text-sky-600',
  purple: 'text-purple-600',
  green: 'text-green-600',
  orange: 'text-orange-600',
  gray: 'text-gray-600',
};

/**
 * Get icon, color, and label information for a recipe type.
 * @param {string} recipeType - The recipe type value
 * @returns {Object} Object with icon, color, colorClass, label (short), and fullLabel properties
 */
export function getRecipeTypeInfo(recipeType) {
  let info;
  switch (recipeType) {
    case RecipeType.CLUSTER:
      info = {
        icon: ServerIcon,
        color: 'sky',
        label: 'Cluster',
        fullLabel: 'Cluster',
      };
      break;
    case RecipeType.JOB:
      info = {
        icon: BriefcaseIcon,
        color: 'purple',
        label: 'Job',
        fullLabel: 'Managed Job',
      };
      break;
    case RecipeType.VOLUME:
      info = {
        icon: DatabaseIcon,
        color: 'green',
        label: 'Volume',
        fullLabel: 'Volume',
      };
      break;
    case RecipeType.POOL:
      info = {
        icon: LayersIcon,
        color: 'orange',
        label: 'Pool',
        fullLabel: 'Job Pool',
      };
      break;
    default:
      throw new Error(`Invalid recipe type: ${recipeType}`);
  }
  // Add the Tailwind color class
  info.colorClass = COLOR_CLASS_MAP[info.color] || 'text-gray-600';
  return info;
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
