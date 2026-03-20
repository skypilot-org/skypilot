/**
 * Recipe type constants for the Recipe Hub.
 *
 * These values must match the RecipeType enum in sky/recipes/utils.py.
 * Plugin-provided recipe types are registered at runtime via
 * api.registerRecipeType() and are NOT listed here.
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
 * List of all built-in recipe types.
 */
export const BUILTIN_RECIPE_TYPES = Object.freeze(Object.values(RecipeType));

/**
 * Get the list of recipe types visible in the UI.
 * Combines built-in types with any plugin-registered types.
 * @param {Array} pluginRecipeTypes - Array of plugin-registered recipe type objects
 * @returns {Array<string>} List of visible recipe type values
 */
export function getVisibleRecipeTypes(pluginRecipeTypes = []) {
  const pluginTypeIds = pluginRecipeTypes.map((t) => t.id);
  return [...BUILTIN_RECIPE_TYPES, ...pluginTypeIds];
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
 * Checks built-in types first, then falls back to plugin-registered types.
 * @param {string} recipeType - The recipe type value
 * @param {Array} pluginRecipeTypes - Array of plugin-registered recipe type objects
 * @returns {Object} Object with icon, color, colorClass, label (short), and fullLabel properties
 */
export function getRecipeTypeInfo(recipeType, pluginRecipeTypes = []) {
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
    default: {
      // Check plugin-registered recipe types
      const pluginType = pluginRecipeTypes.find((t) => t.id === recipeType);
      if (pluginType) {
        info = {
          icon: pluginType.icon || FileCode,
          color: pluginType.color || 'gray',
          label: pluginType.label,
          fullLabel: pluginType.fullLabel || pluginType.label,
        };
        break;
      }
      // Unknown type — use generic fallback
      info = {
        icon: FileCode,
        color: 'gray',
        label: capitalizeWords(recipeType),
        fullLabel: capitalizeWords(recipeType),
      };
      break;
    }
  }
  // Add the Tailwind color class
  info.colorClass = COLOR_CLASS_MAP[info.color] || 'text-gray-600';
  return info;
}

/**
 * Generate the CLI launch command for a recipe.
 * Returns null for plugin-provided recipe types (they handle launching
 * via PluginSlot).
 * @param {string} recipeType - The recipe type value
 * @param {string} recipeName - The recipe's unique name
 * @returns {string|null} The CLI command to launch this recipe, or null
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
      // Plugin-provided types handle launching via PluginSlot
      return null;
  }
}
