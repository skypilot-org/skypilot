'use client';

import { useEffect, useRef } from 'react';
import { ENDPOINT } from '@/data/connectors/constants';

/**
 * Hook to poll /status endpoint with exponential backoff during upgrades
 * and reload the page when the server is back up
 */
export function useUpgradePolling(isUpgrading) {
  const timeoutRef = useRef(null);
  const attemptRef = useRef(0);

  useEffect(() => {
    if (!isUpgrading) {
      // Clear any pending timeouts when not upgrading
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
      attemptRef.current = 0;
      return;
    }

    // Start polling when upgrade is detected
    const poll = async () => {
      try {
        const baseUrl = window.location.origin;
        const fullUrl = `${baseUrl}${ENDPOINT}/status`;

        const response = await fetch(fullUrl, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({}),
        });

        if (response.ok || response.status < 500) {
          // Server is back up - reload the page
          console.log('Server is back online, reloading page...');
          window.location.reload();
          return;
        }

        // Server still down, schedule next attempt with exponential backoff
        scheduleNextPoll();
      } catch (error) {
        // Network error or other failure, continue polling
        console.debug('Status check failed, will retry:', error);
        scheduleNextPoll();
      }
    };

    const scheduleNextPoll = () => {
      attemptRef.current += 1;
      // Exponential backoff: 1s, 2s, 4s, 8s, 16s, max 30s
      const backoffTime = Math.min(
        1000 * Math.pow(2, attemptRef.current - 1),
        30000
      );

      console.debug(
        `Scheduling next upgrade poll in ${backoffTime}ms (attempt ${attemptRef.current})`
      );

      timeoutRef.current = setTimeout(poll, backoffTime);
    };

    // Start first poll immediately
    poll();

    // Cleanup on unmount
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, [isUpgrading]);
}

