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
    const dataBeforeEnhancement = enhancedData;
    try {
      const result = await enhancement.enhance(
        dataBeforeEnhancement,
        enhancementContext
      );

      // Validate that enhancement returned an array
      if (!Array.isArray(result)) {
        console.error(
          `[Plugin] Data enhancement ${enhancement.id} did not return an array. Skipping.`
        );
        continue;
      }

      // Validate that array length matches (enhancements should not add/remove items)
      if (result.length !== dataBeforeEnhancement.length) {
        console.warn(
          `[Plugin] Data enhancement ${enhancement.id} changed array length from ${dataBeforeEnhancement.length} to ${result.length}. This is not recommended.`
        );
      }
      enhancedData = result;
    } catch (error) {
      console.error(
        `[Plugin] Data enhancement ${enhancement.id} failed:`,
        error
      );
      // On error, enhancedData is not updated, so it remains as it was before this failed enhancement.
      // This correctly skips the failed enhancement and continues the chain.
    }
  }

  return enhancedData;
}
