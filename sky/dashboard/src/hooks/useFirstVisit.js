import { useState, useEffect } from 'react';

const FIRST_VISIT_KEY = 'skypilot-dashboard-first-visit';
const TOUR_COMPLETED_KEY = 'skypilot-dashboard-tour-completed';

export function useFirstVisit(options = {}) {
  const { enabled = true } = options;
  const [isFirstVisit, setIsFirstVisit] = useState(false);
  const [tourCompleted, setTourCompleted] = useState(false);
  const [isClient, setIsClient] = useState(false);

  useEffect(() => {
    setIsClient(true);
  }, []);

  useEffect(() => {
    if (!isClient || !enabled) return;

    // Check if this is the first visit using sessionStorage for incognito compatibility
    const hasVisited = sessionStorage.getItem(FIRST_VISIT_KEY);
    const hasTourCompleted = localStorage.getItem(TOUR_COMPLETED_KEY);

    if (!hasVisited) {
      setIsFirstVisit(true);
      sessionStorage.setItem(FIRST_VISIT_KEY, 'true');
    } else {
      setIsFirstVisit(false);
    }

    if (hasTourCompleted) {
      setTourCompleted(true);
    }
  }, [isClient, enabled]);

  const markTourCompleted = () => {
    localStorage.setItem(TOUR_COMPLETED_KEY, 'true');
    setTourCompleted(true);
  };

  const resetFirstVisit = () => {
    sessionStorage.removeItem(FIRST_VISIT_KEY);
    localStorage.removeItem(TOUR_COMPLETED_KEY);
    setIsFirstVisit(true);
    setTourCompleted(false);
  };

  const shouldShowTourPrompt =
    isClient && enabled && isFirstVisit && !tourCompleted;
  const shouldPulseHelpButton =
    isClient && enabled && isFirstVisit && !tourCompleted;

  return {
    isFirstVisit: isClient ? isFirstVisit : false,
    tourCompleted: isClient ? tourCompleted : false,
    shouldShowTourPrompt,
    shouldPulseHelpButton,
    markTourCompleted,
    resetFirstVisit,
    isClient,
  };
}
