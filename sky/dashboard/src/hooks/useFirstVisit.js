import { useState, useEffect } from 'react';

const FIRST_VISIT_KEY = 'skypilot-dashboard-first-visit';
const TOUR_COMPLETED_KEY = 'skypilot-dashboard-tour-completed';

export function useFirstVisit() {
  const [isFirstVisit, setIsFirstVisit] = useState(false);
  const [tourCompleted, setTourCompleted] = useState(false);
  const [isClient, setIsClient] = useState(false);

  useEffect(() => {
    setIsClient(true);

    // Check if this is the first visit using localStorage to share across tabs
    const hasVisited = localStorage.getItem(FIRST_VISIT_KEY);
    const hasTourCompleted = localStorage.getItem(TOUR_COMPLETED_KEY);

    if (!hasVisited) {
      setIsFirstVisit(true);
      localStorage.setItem(FIRST_VISIT_KEY, 'true');
    }

    if (hasTourCompleted) {
      setTourCompleted(true);
    }
  }, []);

  const markTourCompleted = () => {
    localStorage.setItem(TOUR_COMPLETED_KEY, 'true');
    setTourCompleted(true);
  };

  const resetFirstVisit = () => {
    localStorage.removeItem(FIRST_VISIT_KEY);
    localStorage.removeItem(TOUR_COMPLETED_KEY);
    setIsFirstVisit(true);
    setTourCompleted(false);
  };

  const shouldShowTourPrompt = isClient && isFirstVisit && !tourCompleted;
  const shouldPulseHelpButton = isClient && isFirstVisit && !tourCompleted;

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
