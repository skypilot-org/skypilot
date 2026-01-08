/**
 * Performance Benchmark Utility for SkyPilot Dashboard
 *
 * This module provides tools to measure and log performance metrics
 * for both frontend rendering and backend API response times.
 *
 * Usage:
 * 1. Import in browser console: window.dashboardPerf = (await import('./lib/performance-benchmark.js')).default
 * 2. Or import in component: import { measureApiLatency, measureRenderTime } from '@/lib/performance-benchmark'
 */

// Store for benchmark results
const benchmarkResults = {
  apiCalls: [],
  renders: [],
  summary: null,
};

/**
 * Measure API endpoint latency
 * @param {string} endpoint - API endpoint path
 * @param {object} options - Fetch options
 * @returns {Promise<{data: any, latency: number}>}
 */
export async function measureApiLatency(endpoint, options = {}) {
  const startTime = performance.now();

  try {
    const response = await fetch(endpoint, options);
    const endTime = performance.now();
    const latency = endTime - startTime;

    const result = {
      endpoint,
      latency,
      status: response.status,
      timestamp: new Date().toISOString(),
    };

    benchmarkResults.apiCalls.push(result);

    console.log(`[PerfBenchmark] API ${endpoint}: ${latency.toFixed(2)}ms (status: ${response.status})`);

    return {
      data: response.ok ? await response.json() : null,
      latency,
      status: response.status,
    };
  } catch (error) {
    const endTime = performance.now();
    const latency = endTime - startTime;

    console.error(`[PerfBenchmark] API ${endpoint} failed after ${latency.toFixed(2)}ms:`, error.message);

    return {
      data: null,
      latency,
      error: error.message,
    };
  }
}

/**
 * Measure render time of a component
 * @param {string} componentName - Name of the component
 * @param {Function} renderFn - Function that triggers the render
 * @returns {Promise<number>} - Render time in milliseconds
 */
export async function measureRenderTime(componentName, renderFn) {
  const startTime = performance.now();

  await renderFn();

  // Wait for next frame to ensure render is complete
  await new Promise(resolve => requestAnimationFrame(resolve));

  const endTime = performance.now();
  const renderTime = endTime - startTime;

  const result = {
    component: componentName,
    renderTime,
    timestamp: new Date().toISOString(),
  };

  benchmarkResults.renders.push(result);

  console.log(`[PerfBenchmark] Render ${componentName}: ${renderTime.toFixed(2)}ms`);

  return renderTime;
}

/**
 * Run a comprehensive benchmark of dashboard API endpoints
 * @returns {Promise<object>} - Benchmark results
 */
export async function runApiLatencyBenchmark() {
  console.log('[PerfBenchmark] Starting API latency benchmark...');

  const endpoints = [
    { name: 'Health Check', path: '/api/health' },
    { name: 'Clusters', path: '/api/status', method: 'POST', body: {} },
    { name: 'Jobs Queue', path: '/api/jobs/queue/v2', method: 'POST', body: { allUsers: true, limit: 10 } },
    { name: 'Workspaces', path: '/api/workspaces', method: 'POST', body: {} },
    { name: 'Users', path: '/api/users', method: 'GET' },
    { name: 'Pool Status', path: '/api/jobs/pool_status', method: 'POST', body: {} },
  ];

  const results = [];

  for (const endpoint of endpoints) {
    const options = endpoint.method === 'POST'
      ? {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(endpoint.body || {}),
        }
      : { method: 'GET' };

    // Run 3 times and average
    const latencies = [];
    for (let i = 0; i < 3; i++) {
      const { latency } = await measureApiLatency(endpoint.path, options);
      latencies.push(latency);
      // Small delay between requests
      await new Promise(resolve => setTimeout(resolve, 100));
    }

    const avgLatency = latencies.reduce((a, b) => a + b, 0) / latencies.length;
    const minLatency = Math.min(...latencies);
    const maxLatency = Math.max(...latencies);

    results.push({
      name: endpoint.name,
      path: endpoint.path,
      avgLatency,
      minLatency,
      maxLatency,
      samples: latencies,
    });
  }

  // Generate summary
  const summary = {
    timestamp: new Date().toISOString(),
    results,
    slowestEndpoint: results.reduce((a, b) => a.avgLatency > b.avgLatency ? a : b),
    fastestEndpoint: results.reduce((a, b) => a.avgLatency < b.avgLatency ? a : b),
    totalAvg: results.reduce((sum, r) => sum + r.avgLatency, 0) / results.length,
  };

  benchmarkResults.summary = summary;

  console.log('\n[PerfBenchmark] API Latency Summary:');
  console.table(results.map(r => ({
    Endpoint: r.name,
    'Avg (ms)': r.avgLatency.toFixed(2),
    'Min (ms)': r.minLatency.toFixed(2),
    'Max (ms)': r.maxLatency.toFixed(2),
  })));

  console.log(`\nSlowest: ${summary.slowestEndpoint.name} (${summary.slowestEndpoint.avgLatency.toFixed(2)}ms)`);
  console.log(`Fastest: ${summary.fastestEndpoint.name} (${summary.fastestEndpoint.avgLatency.toFixed(2)}ms)`);

  return summary;
}

/**
 * Measure cache effectiveness
 * @returns {Promise<object>} - Cache stats
 */
export async function measureCacheEffectiveness() {
  console.log('[PerfBenchmark] Measuring cache effectiveness...');

  // First call (likely cache miss)
  const firstCall = await measureApiLatency('/api/status', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });

  // Wait a bit
  await new Promise(resolve => setTimeout(resolve, 500));

  // Second call (should hit cache)
  const secondCall = await measureApiLatency('/api/status', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });

  const improvement = ((firstCall.latency - secondCall.latency) / firstCall.latency) * 100;

  console.log(`\n[PerfBenchmark] Cache Effectiveness:`);
  console.log(`  First call: ${firstCall.latency.toFixed(2)}ms`);
  console.log(`  Second call: ${secondCall.latency.toFixed(2)}ms`);
  console.log(`  Improvement: ${improvement.toFixed(1)}%`);

  return {
    firstCall: firstCall.latency,
    secondCall: secondCall.latency,
    improvement,
  };
}

/**
 * Get all benchmark results
 * @returns {object} - All benchmark results
 */
export function getBenchmarkResults() {
  return { ...benchmarkResults };
}

/**
 * Clear benchmark results
 */
export function clearBenchmarkResults() {
  benchmarkResults.apiCalls = [];
  benchmarkResults.renders = [];
  benchmarkResults.summary = null;
  console.log('[PerfBenchmark] Results cleared');
}

/**
 * Performance observer for long tasks
 */
export function startLongTaskObserver() {
  if (typeof PerformanceObserver === 'undefined') {
    console.warn('[PerfBenchmark] PerformanceObserver not available');
    return null;
  }

  const observer = new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) {
      if (entry.duration > 50) { // Tasks longer than 50ms are considered "long"
        console.warn(`[PerfBenchmark] Long task detected: ${entry.duration.toFixed(2)}ms`, entry);
      }
    }
  });

  try {
    observer.observe({ entryTypes: ['longtask'] });
    console.log('[PerfBenchmark] Long task observer started');
    return observer;
  } catch (e) {
    console.warn('[PerfBenchmark] Could not start long task observer:', e.message);
    return null;
  }
}

/**
 * Measure component re-render count
 * Use this as a React hook in components
 */
export function useRenderCount(componentName) {
  const countRef = useRef(0);
  countRef.current++;

  useEffect(() => {
    console.log(`[PerfBenchmark] ${componentName} render count: ${countRef.current}`);
  });

  return countRef.current;
}

// Make available globally for debugging in browser console
if (typeof window !== 'undefined') {
  window.dashboardPerf = {
    measureApiLatency,
    measureRenderTime,
    runApiLatencyBenchmark,
    measureCacheEffectiveness,
    getBenchmarkResults,
    clearBenchmarkResults,
    startLongTaskObserver,
  };
}

export default {
  measureApiLatency,
  measureRenderTime,
  runApiLatencyBenchmark,
  measureCacheEffectiveness,
  getBenchmarkResults,
  clearBenchmarkResults,
  startLongTaskObserver,
};
