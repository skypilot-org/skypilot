/**
 * Data enhancement execution utility
 * Applies plugin data enhancements to data returned by connectors
 */

import { getDataEnhancements } from './PluginProvider';
import dashboardCache from '@/lib/cache';

/**
 * Apply data enhancements to a dataset
 * @param {Array} data - The processed data array
 * @param {string} dataSource - The data source name (e.g., 'jobs', 'clusters')
 * @param {Object} [context={}] - Optional context to pass to enhancements
 * @param {Array} [context.rawData] - Raw backend response data (optional)
 * @param {Object} [context.dashboardCache] - Dashboard cache instance (optional)
 * @returns {Promise<Array>} The enhanced data
 */
export async function applyEnhancements(data, dataSource, context = {}) {
  const enhancements = getDataEnhancements(dataSource);

  if (enhancements.length === 0) {
    return data;
  }

  // Build enhancement context
  const enhancementContext = {
    dashboardCache: context.dashboardCache || dashboardCache,
    getOriginalData: () => Promise.resolve(data),
    rawData: context.rawData || null, // Raw backend response for field extraction
    ...context,
  };

  let enhancedData = data;

  // Execute enhancements sequentially (each receives data from previous)
  for (const enhancement of enhancements) {
    try {
      enhancedData = await enhancement.enhance(
        enhancedData,
        enhancementContext
      );

      // Validate that enhancement returned an array
      if (!Array.isArray(enhancedData)) {
        console.error(
          `[Plugin] Data enhancement ${enhancement.id} did not return an array`
        );
        enhancedData = data; // Fallback to previous data
        continue;
      }

      // Validate that array length matches (enhancements should not add/remove items)
      if (enhancedData.length !== data.length) {
        console.warn(
          `[Plugin] Data enhancement ${enhancement.id} changed array length from ${data.length} to ${enhancedData.length}. This is not recommended.`
        );
      }
    } catch (error) {
      console.error(
        `[Plugin] Data enhancement ${enhancement.id} failed:`,
        error
      );
      // Continue with previous data if enhancement fails
      // Don't break the chain - other enhancements should still run
    }
  }

  return enhancedData;
}
