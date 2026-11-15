import { useCallback, useState, useEffect } from 'react';

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

    // The visited marker is NOT written here. It is consumed by
    // markTourPromptShown() when the first-visit prompt actually appears.
    // Writing it at mount meant any rebuild of the app tree before the prompt
    // showed -- e.g. an app.providers wrapper registering after the bootstrap
    // backstop opened the gate in _app.js, which remounts everything below it
    // -- silently swallowed the tour: the rebuilt tree read the marker,
    // decided this was not a first visit, and no later visit could bring the
    // prompt back.
    //
    // A completed tour counts as visited even without the marker: the tour is
    // auto-started on first visit, so it can complete before the prompt ever
    // shows, and must not auto-start again on the next visit.
    if (!hasVisited && !hasTourCompleted) {
      setIsFirstVisit(true);
    }

    if (hasTourCompleted) {
      setTourCompleted(true);
    }
  }, []);

  // Called when the first-visit tour actually starts. TourProvider consumes
  // the visit at that moment: the started tour is a seen welcome, and the
  // marker must already be set before WelcomeNotification mounts (it is
  // chunk-loaded) so the two surfaces never stack.
  const markTourStarted = useCallback(() => {
    localStorage.setItem(FIRST_VISIT_KEY, 'true');
  }, []);

  // Called when the first-visit prompt becomes visible. Returns false when
  // the prompt must not be shown after all (the tour was completed while the
  // show delay was pending, e.g. by the auto-started tour or another tab).
  const markTourPromptShown = useCallback(() => {
    if (localStorage.getItem(TOUR_COMPLETED_KEY)) {
      return false;
    }
    localStorage.setItem(FIRST_VISIT_KEY, 'true');
    return true;
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
    markTourStarted,
    markTourPromptShown,
    markTourCompleted,
    resetFirstVisit,
    isClient,
  };
}
