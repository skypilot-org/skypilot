/**
 * Performance tests for React.memo optimizations
 *
 * These tests verify that memoized components don't re-render unnecessarily
 * when their props haven't changed, demonstrating the performance improvements.
 */

import React, { useState, memo, useCallback } from 'react';
import { render, screen, fireEvent, act } from '@testing-library/react';

// Mock next/router
jest.mock('next/router', () => ({
  useRouter: () => ({
    push: jest.fn(),
    pathname: '/jobs',
    query: {},
  }),
}));

// Mock next/link
jest.mock('next/link', () => {
  // eslint-disable-next-line react/display-name
  return ({ children, href }) => <a href={href}>{children}</a>;
});

// ============================================
// Test utilities for counting renders
// ============================================

function createRenderCounter() {
  let count = 0;
  return {
    increment: () => ++count,
    get: () => count,
    reset: () => (count = 0),
  };
}

// ============================================
// Test 1: Non-memoized vs Memoized Row Component
// ============================================

describe('Memoization Performance Tests', () => {
  describe('Table Row Component Memoization', () => {
    const renderCounterNonMemoized = createRenderCounter();
    const renderCounterMemoized = createRenderCounter();

    // Non-memoized row component (old pattern)
    const NonMemoizedRow = ({ item, onClick }) => {
      renderCounterNonMemoized.increment();
      return (
        <tr data-testid={`non-memo-row-${item.id}`}>
          <td>{item.name}</td>
          <td>
            <button onClick={() => onClick(item.id)}>Action</button>
          </td>
        </tr>
      );
    };

    // Memoized row component (new pattern)
    const MemoizedRow = memo(function MemoizedRow({ item, onClick }) {
      renderCounterMemoized.increment();
      return (
        <tr data-testid={`memo-row-${item.id}`}>
          <td>{item.name}</td>
          <td>
            <button onClick={() => onClick(item.id)}>Action</button>
          </td>
        </tr>
      );
    });

    // Items defined outside component to ensure stable reference
    // (simulates real-world data from API/props)
    const tableItems = [
      { id: 1, name: 'Job 1' },
      { id: 2, name: 'Job 2' },
      { id: 3, name: 'Job 3' },
    ];

    // Parent component that updates unrelated state
    const TableWithUnrelatedState = ({ RowComponent, renderCounter }) => {
      const [unrelatedState, setUnrelatedState] = useState(0);

      // Stable callback for memoized version
      const handleClick = useCallback((id) => {
        console.log('clicked', id);
      }, []);

      return (
        <div>
          <button
            data-testid="update-unrelated"
            onClick={() => setUnrelatedState((s) => s + 1)}
          >
            Update Unrelated State ({unrelatedState})
          </button>
          <table>
            <tbody>
              {tableItems.map((item) => (
                <RowComponent key={item.id} item={item} onClick={handleClick} />
              ))}
            </tbody>
          </table>
          <div data-testid="render-count">{renderCounter.get()}</div>
        </div>
      );
    };

    beforeEach(() => {
      renderCounterNonMemoized.reset();
      renderCounterMemoized.reset();
    });

    test('Non-memoized rows re-render on every parent state change', () => {
      render(
        <TableWithUnrelatedState
          RowComponent={NonMemoizedRow}
          renderCounter={renderCounterNonMemoized}
        />
      );

      // Initial render: 3 rows = 3 renders
      expect(renderCounterNonMemoized.get()).toBe(3);

      // Click button to update unrelated state
      fireEvent.click(screen.getByTestId('update-unrelated'));

      // Non-memoized: all 3 rows re-render = 6 total
      expect(renderCounterNonMemoized.get()).toBe(6);

      // Click again
      fireEvent.click(screen.getByTestId('update-unrelated'));

      // 9 total renders for 3 state updates
      expect(renderCounterNonMemoized.get()).toBe(9);
    });

    test('Memoized rows do NOT re-render on unrelated parent state changes', () => {
      render(
        <TableWithUnrelatedState
          RowComponent={MemoizedRow}
          renderCounter={renderCounterMemoized}
        />
      );

      // Initial render: 3 rows = 3 renders
      expect(renderCounterMemoized.get()).toBe(3);

      // Click button to update unrelated state
      fireEvent.click(screen.getByTestId('update-unrelated'));

      // Memoized: NO additional renders, still 3
      expect(renderCounterMemoized.get()).toBe(3);

      // Click again
      fireEvent.click(screen.getByTestId('update-unrelated'));

      // Still 3 total renders - memoization prevents re-renders
      expect(renderCounterMemoized.get()).toBe(3);
    });

    test('Performance comparison summary', () => {
      // Reset counters
      renderCounterNonMemoized.reset();
      renderCounterMemoized.reset();

      // Render non-memoized
      const { unmount: unmountNonMemo } = render(
        <TableWithUnrelatedState
          RowComponent={NonMemoizedRow}
          renderCounter={renderCounterNonMemoized}
        />
      );

      // Simulate 10 state updates (like polling refreshes)
      for (let i = 0; i < 10; i++) {
        fireEvent.click(screen.getByTestId('update-unrelated'));
      }

      const nonMemoizedRenders = renderCounterNonMemoized.get();
      unmountNonMemo();

      // Render memoized
      render(
        <TableWithUnrelatedState
          RowComponent={MemoizedRow}
          renderCounter={renderCounterMemoized}
        />
      );

      // Same 10 state updates
      for (let i = 0; i < 10; i++) {
        fireEvent.click(screen.getByTestId('update-unrelated'));
      }

      const memoizedRenders = renderCounterMemoized.get();

      // Log comparison
      console.log('\n=== PERFORMANCE COMPARISON ===');
      console.log(`Non-memoized renders: ${nonMemoizedRenders}`);
      console.log(`Memoized renders: ${memoizedRenders}`);
      console.log(
        `Reduction: ${(((nonMemoizedRenders - memoizedRenders) / nonMemoizedRenders) * 100).toFixed(1)}%`
      );
      console.log('==============================\n');

      // Non-memoized: 3 initial + (10 updates * 3 rows) = 33 renders
      expect(nonMemoizedRenders).toBe(33);

      // Memoized: 3 initial renders only
      expect(memoizedRenders).toBe(3);

      // Verify significant reduction (should be ~91% reduction)
      const reduction =
        ((nonMemoizedRenders - memoizedRenders) / nonMemoizedRenders) * 100;
      expect(reduction).toBeGreaterThan(90);
    });
  });

  describe('useCallback optimization', () => {
    const renderCounter = createRenderCounter();

    const ChildWithCallback = memo(function ChildWithCallback({ onClick }) {
      renderCounter.increment();
      return <button onClick={onClick}>Click me</button>;
    });

    test('Stable callbacks prevent child re-renders', () => {
      const ParentWithStableCallback = () => {
        const [count, setCount] = useState(0);
        // Stable callback with useCallback
        const handleClick = useCallback(() => {
          console.log('clicked');
        }, []);

        return (
          <div>
            <button
              data-testid="increment"
              onClick={() => setCount((c) => c + 1)}
            >
              Count: {count}
            </button>
            <ChildWithCallback onClick={handleClick} />
          </div>
        );
      };

      renderCounter.reset();
      render(<ParentWithStableCallback />);

      // Initial render
      expect(renderCounter.get()).toBe(1);

      // Update parent state multiple times
      for (let i = 0; i < 5; i++) {
        fireEvent.click(screen.getByTestId('increment'));
      }

      // Child should NOT re-render because callback reference is stable
      expect(renderCounter.get()).toBe(1);

      console.log('\n=== useCallback OPTIMIZATION ===');
      console.log('Child renders with stable callback: 1 (only initial)');
      console.log('Without useCallback would be: 6 (initial + 5 updates)');
      console.log('================================\n');
    });

    test('Unstable callbacks cause unnecessary re-renders', () => {
      const unstableRenderCounter = createRenderCounter();

      const ChildForUnstable = memo(function ChildForUnstable({ onClick }) {
        unstableRenderCounter.increment();
        return <button onClick={onClick}>Click me</button>;
      });

      const ParentWithUnstableCallback = () => {
        const [count, setCount] = useState(0);
        // Unstable callback - new function every render
        const handleClick = () => {
          console.log('clicked');
        };

        return (
          <div>
            <button
              data-testid="increment-unstable"
              onClick={() => setCount((c) => c + 1)}
            >
              Count: {count}
            </button>
            <ChildForUnstable onClick={handleClick} />
          </div>
        );
      };

      unstableRenderCounter.reset();
      render(<ParentWithUnstableCallback />);

      // Initial render
      expect(unstableRenderCounter.get()).toBe(1);

      // Update parent state multiple times
      for (let i = 0; i < 5; i++) {
        fireEvent.click(screen.getByTestId('increment-unstable'));
      }

      // Child WILL re-render because callback reference changes each time
      expect(unstableRenderCounter.get()).toBe(6);
    });
  });

  describe('Large table simulation', () => {
    test('Memoization scales with table size', () => {
      const ROW_COUNT = 100; // Simulate 100 rows
      const UPDATE_COUNT = 20; // Simulate 20 polling updates

      const nonMemoRenderCount = createRenderCounter();
      const memoRenderCount = createRenderCounter();

      const NonMemoRow = ({ item }) => {
        nonMemoRenderCount.increment();
        return (
          <tr>
            <td>{item.id}</td>
          </tr>
        );
      };

      const MemoRow = memo(function MemoRow({ item }) {
        memoRenderCount.increment();
        return (
          <tr>
            <td>{item.id}</td>
          </tr>
        );
      });

      const items = Array.from({ length: ROW_COUNT }, (_, i) => ({ id: i }));

      const TableSimulation = ({ Row, counter }) => {
        const [tick, setTick] = useState(0);
        return (
          <div>
            <button data-testid="tick" onClick={() => setTick((t) => t + 1)}>
              Tick {tick}
            </button>
            <table>
              <tbody>
                {items.map((item) => (
                  <Row key={item.id} item={item} />
                ))}
              </tbody>
            </table>
          </div>
        );
      };

      // Test non-memoized
      nonMemoRenderCount.reset();
      const { unmount: unmountNonMemo } = render(
        <TableSimulation Row={NonMemoRow} counter={nonMemoRenderCount} />
      );

      for (let i = 0; i < UPDATE_COUNT; i++) {
        fireEvent.click(screen.getByTestId('tick'));
      }

      const totalNonMemoRenders = nonMemoRenderCount.get();
      unmountNonMemo();

      // Test memoized
      memoRenderCount.reset();
      render(<TableSimulation Row={MemoRow} counter={memoRenderCount} />);

      for (let i = 0; i < UPDATE_COUNT; i++) {
        fireEvent.click(screen.getByTestId('tick'));
      }

      const totalMemoRenders = memoRenderCount.get();

      console.log('\n=== LARGE TABLE SIMULATION ===');
      console.log(`Table size: ${ROW_COUNT} rows`);
      console.log(`Update cycles: ${UPDATE_COUNT}`);
      console.log(`Non-memoized total renders: ${totalNonMemoRenders}`);
      console.log(`Memoized total renders: ${totalMemoRenders}`);
      console.log(
        `Renders saved: ${totalNonMemoRenders - totalMemoRenders} (${(((totalNonMemoRenders - totalMemoRenders) / totalNonMemoRenders) * 100).toFixed(1)}%)`
      );
      console.log('==============================\n');

      // Non-memoized: 100 initial + (20 updates * 100 rows) = 2100 renders
      expect(totalNonMemoRenders).toBe(ROW_COUNT * (UPDATE_COUNT + 1));

      // Memoized: only 100 initial renders
      expect(totalMemoRenders).toBe(ROW_COUNT);

      // Calculate saved renders
      const savedRenders = totalNonMemoRenders - totalMemoRenders;
      expect(savedRenders).toBe(ROW_COUNT * UPDATE_COUNT);
    });
  });

  describe('Timing measurements', () => {
    test('Measures actual time difference for render cycles', () => {
      const ROW_COUNT = 50; // Realistic: max page size option in dashboard
      const UPDATE_COUNT = 30; // ~1 minute of polling at 2s intervals

      // Simulate a more realistic row with multiple elements
      const NonMemoRow = ({ item, onAction }) => {
        return (
          <tr>
            <td>{item.id}</td>
            <td>{item.name}</td>
            <td>{item.status}</td>
            <td>{item.timestamp}</td>
            <td>
              <button onClick={() => onAction(item.id)}>Action 1</button>
              <button onClick={() => onAction(item.id)}>Action 2</button>
            </td>
          </tr>
        );
      };

      const MemoRow = memo(function MemoRow({ item, onAction }) {
        return (
          <tr>
            <td>{item.id}</td>
            <td>{item.name}</td>
            <td>{item.status}</td>
            <td>{item.timestamp}</td>
            <td>
              <button onClick={() => onAction(item.id)}>Action 1</button>
              <button onClick={() => onAction(item.id)}>Action 2</button>
            </td>
          </tr>
        );
      });

      const items = Array.from({ length: ROW_COUNT }, (_, i) => ({
        id: i,
        name: `Job ${i}`,
        status: i % 2 === 0 ? 'Running' : 'Completed',
        timestamp: new Date().toISOString(),
      }));

      const TableWithTiming = ({ Row }) => {
        const [tick, setTick] = useState(0);
        const handleAction = useCallback((id) => {
          console.log('action', id);
        }, []);

        return (
          <div>
            <button data-testid="tick-timing" onClick={() => setTick((t) => t + 1)}>
              Tick {tick}
            </button>
            <table>
              <tbody>
                {items.map((item) => (
                  <Row key={item.id} item={item} onAction={handleAction} />
                ))}
              </tbody>
            </table>
          </div>
        );
      };

      // Measure non-memoized timing
      const { unmount: unmountNonMemo } = render(
        <TableWithTiming Row={NonMemoRow} />
      );

      const nonMemoStart = performance.now();
      for (let i = 0; i < UPDATE_COUNT; i++) {
        fireEvent.click(screen.getByTestId('tick-timing'));
      }
      const nonMemoTime = performance.now() - nonMemoStart;
      unmountNonMemo();

      // Measure memoized timing
      render(<TableWithTiming Row={MemoRow} />);

      const memoStart = performance.now();
      for (let i = 0; i < UPDATE_COUNT; i++) {
        fireEvent.click(screen.getByTestId('tick-timing'));
      }
      const memoTime = performance.now() - memoStart;

      const timeSaved = nonMemoTime - memoTime;
      const percentFaster = ((timeSaved / nonMemoTime) * 100);

      console.log('\n========================================');
      console.log('    TIMING COMPARISON (Before vs After)');
      console.log('========================================');
      console.log(`Table size: ${ROW_COUNT} rows`);
      console.log(`Update cycles: ${UPDATE_COUNT}`);
      console.log('----------------------------------------');
      console.log(`BEFORE (Non-memoized): ${nonMemoTime.toFixed(2)}ms`);
      console.log(`AFTER (Memoized):      ${memoTime.toFixed(2)}ms`);
      console.log('----------------------------------------');
      console.log(`Time saved: ${timeSaved.toFixed(2)}ms (${percentFaster.toFixed(1)}% faster)`);
      console.log(`Per update: ${(nonMemoTime / UPDATE_COUNT).toFixed(2)}ms -> ${(memoTime / UPDATE_COUNT).toFixed(2)}ms`);
      console.log('========================================\n');

      // Memoized should be faster
      expect(memoTime).toBeLessThan(nonMemoTime);
    });

    test('Measures time for realistic dashboard scenario', () => {
      // Simulate a dashboard with multiple tables and state updates
      const JOB_COUNT = 50;
      const CLUSTER_COUNT = 20;
      const POLL_CYCLES = 30; // Simulate 30 polling updates (1 minute at 2s intervals)

      const JobRow = memo(function JobRow({ job }) {
        return (
          <tr>
            <td>{job.id}</td>
            <td>{job.name}</td>
            <td>{job.status}</td>
          </tr>
        );
      });

      const ClusterRow = memo(function ClusterRow({ cluster }) {
        return (
          <tr>
            <td>{cluster.id}</td>
            <td>{cluster.name}</td>
            <td>{cluster.status}</td>
          </tr>
        );
      });

      const NonMemoJobRow = ({ job }) => {
        return (
          <tr>
            <td>{job.id}</td>
            <td>{job.name}</td>
            <td>{job.status}</td>
          </tr>
        );
      };

      const NonMemoClusterRow = ({ cluster }) => {
        return (
          <tr>
            <td>{cluster.id}</td>
            <td>{cluster.name}</td>
            <td>{cluster.status}</td>
          </tr>
        );
      };

      const jobs = Array.from({ length: JOB_COUNT }, (_, i) => ({
        id: i,
        name: `Job ${i}`,
        status: 'Running',
      }));

      const clusters = Array.from({ length: CLUSTER_COUNT }, (_, i) => ({
        id: i,
        name: `Cluster ${i}`,
        status: 'UP',
      }));

      const Dashboard = ({ JobRowComp, ClusterRowComp }) => {
        const [pollCount, setPollCount] = useState(0);
        const [filter, setFilter] = useState('');

        return (
          <div>
            <button
              data-testid="poll"
              onClick={() => setPollCount((c) => c + 1)}
            >
              Poll {pollCount}
            </button>
            <input
              data-testid="filter"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
            <h2>Jobs</h2>
            <table>
              <tbody>
                {jobs.map((job) => (
                  <JobRowComp key={job.id} job={job} />
                ))}
              </tbody>
            </table>
            <h2>Clusters</h2>
            <table>
              <tbody>
                {clusters.map((cluster) => (
                  <ClusterRowComp key={cluster.id} cluster={cluster} />
                ))}
              </tbody>
            </table>
          </div>
        );
      };

      // Non-memoized dashboard
      const { unmount: unmountNonMemo } = render(
        <Dashboard JobRowComp={NonMemoJobRow} ClusterRowComp={NonMemoClusterRow} />
      );

      const nonMemoStart = performance.now();
      for (let i = 0; i < POLL_CYCLES; i++) {
        fireEvent.click(screen.getByTestId('poll'));
      }
      // Also simulate some filter typing
      const filterInput = screen.getByTestId('filter');
      fireEvent.change(filterInput, { target: { value: 'test' } });
      fireEvent.change(filterInput, { target: { value: 'test2' } });
      fireEvent.change(filterInput, { target: { value: '' } });
      const nonMemoTime = performance.now() - nonMemoStart;
      unmountNonMemo();

      // Memoized dashboard
      render(<Dashboard JobRowComp={JobRow} ClusterRowComp={ClusterRow} />);

      const memoStart = performance.now();
      for (let i = 0; i < POLL_CYCLES; i++) {
        fireEvent.click(screen.getByTestId('poll'));
      }
      const filterInput2 = screen.getByTestId('filter');
      fireEvent.change(filterInput2, { target: { value: 'test' } });
      fireEvent.change(filterInput2, { target: { value: 'test2' } });
      fireEvent.change(filterInput2, { target: { value: '' } });
      const memoTime = performance.now() - memoStart;

      const timeSaved = nonMemoTime - memoTime;
      const percentFaster = ((timeSaved / nonMemoTime) * 100);

      console.log('\n================================================');
      console.log('    DASHBOARD SCENARIO (Before vs After)');
      console.log('================================================');
      console.log(`Jobs: ${JOB_COUNT}, Clusters: ${CLUSTER_COUNT}`);
      console.log(`Poll cycles: ${POLL_CYCLES} + 3 filter changes`);
      console.log('------------------------------------------------');
      console.log(`BEFORE (Non-memoized): ${nonMemoTime.toFixed(2)}ms`);
      console.log(`AFTER (Memoized):      ${memoTime.toFixed(2)}ms`);
      console.log('------------------------------------------------');
      console.log(`Time saved: ${timeSaved.toFixed(2)}ms (${percentFaster.toFixed(1)}% faster)`);
      console.log('================================================\n');

      // Memoized should be faster
      expect(memoTime).toBeLessThan(nonMemoTime);
    });
  });
});
